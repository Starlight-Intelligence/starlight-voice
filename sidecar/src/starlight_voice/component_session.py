"""One bounded component session on a host-owned async loop; no discovery startup."""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import os
import re
import secrets
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version

from .adapters.openrouter_speech import OpenRouterSpeech
from .config import Settings, openrouter_inference_key
from .native_bridge import BridgeError, NativeBridge

_STARTUP_CLOSE_SECONDS = 2


def _present(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _browser_sdk_ready() -> bool:
    try:
        parts = version("pipecat-ai").split(".")
        return int(parts[0]) == 1 and int(parts[1]) >= 12
    except (PackageNotFoundError, ValueError, IndexError):
        return False


def component_spec(settings: Settings | None = None, *, present=_present, sdk_ready=_browser_sdk_ready) -> dict:
    settings = settings or Settings.from_env()
    valid_voice = bool(re.fullmatch(r"[A-Za-z0-9_-]{1,128}", settings.tts_voice_id))
    supported_stt = settings.stt_engine == "groq-openrouter" or (
        settings.stt_engine == "openrouter-transcription" and settings.stt_model == "x-ai/grok-stt-1.0"
    )
    supported = supported_stt and (
        settings.tts_engine == "openrouter-speech"
        and settings.tts_model in OpenRouterSpeech.MODELS
        and (settings.tts_model != "x-ai/grok-voice-tts-1.0" or settings.tts_voice_id in OpenRouterSpeech.GROK_VOICES)
        or settings.tts_engine == "elevenlabs"
        and settings.tts_model in {"eleven_v4_turbo", "eleven_v3_conversational", "eleven_flash_v2_5",
                                  "eleven_flash_v2", "eleven_multilingual_v2"}
    )
    keys = ["GROQ_API_KEY"] if settings.stt_engine == "groq-openrouter" else []
    if settings.tts_engine == "elevenlabs":
        keys.append("ELEVENLABS_API_KEY")
    configured = valid_voice and supported and bool(openrouter_inference_key()) and all(
        bool(os.environ.get(key, "").strip()) and os.environ.get(key, "").strip() != "*not found*" for key in keys
    ) and sdk_ready() and all(present(name) for name in (
        "pipecat", "aiortc", "onnxruntime", "openai", "websockets", "httpx",
        *(("groq",) if settings.stt_engine == "groq-openrouter" else ()),
    ))
    return {"id": "elevenlabs", "transport": "smallwebrtc", "model": settings.tts_model,
            "label": "Grok via OpenRouter" if settings.tts_model == "x-ai/grok-voice-tts-1.0" else "Speech component",
            "voices": [settings.tts_voice_id] if valid_voice and supported else [],
            "configured": bool(configured), "runtime_verified": False,
            "tool_support": "read-only", "local_voice_detection": True}


def validate_offer(payload: dict) -> str:
    if not isinstance(payload, dict) or set(payload) != {"session_id", "sdp", "type"} or payload["type"] != "offer":
        raise BridgeError("Only a session-bound audio SDP offer is accepted")
    sdp = payload["sdp"]
    if (not isinstance(sdp, str) or not sdp.isascii() or not 1 <= len(sdp) <= 48 * 1024
            or "\x00" in sdp or not sdp.startswith("v=0\r\n")):
        raise BridgeError("Component SDP is invalid or exceeds the limit")
    media = [line.split(" ", 1)[0] for line in sdp.splitlines() if line.startswith("m=")]
    if media.count("m=audio") != 1 or media.count("m=application") > 1 or any(
        item not in {"m=audio", "m=application"} for item in media
    ) or sum(line.startswith("a=candidate:") for line in sdp.splitlines()) > 64:
        raise BridgeError("Component conversation accepts one audio track and at most one data channel")
    return hashlib.sha256(sdp.encode("ascii")).hexdigest()


@dataclass
class OwnedSession:
    scope: dict
    settings: Settings
    runtime: object | None = None
    expiry_task: asyncio.Task | None = None
    offer_task: asyncio.Task | None = None
    cleanup_task: asyncio.Task | None = None
    fingerprint: str | None = None
    closing: bool = False
    revoked: bool = False
    expiring: bool = False


class ComponentSessions:
    """All methods run on the same persistent host loop, never per-request loops."""

    def __init__(self, bridge: NativeBridge, *, factory=None, settings=Settings.from_env,
                 spec=component_spec, lifetime=300):
        self.bridge, self.settings, self.spec = bridge, settings, spec
        if not 1 <= lifetime <= 300:
            raise ValueError("Component session lifetime must fit the five-minute boundary")
        self.lifetime = lifetime
        self.factory = factory
        self.current: OwnedSession | None = None
        self.last_stopped = None

    async def start(self, payload: dict) -> dict:
        if not isinstance(payload, dict) or set(payload) != {"provider", "voice"} or payload["provider"] != "elevenlabs":
            raise BridgeError("Choose only the configured component provider and voice")
        settings = self.settings()
        spec = self.spec(settings)
        if payload["voice"] not in spec["voices"] or not spec["configured"]:
            raise BridgeError("Configure component dependencies, provider keys and an explicit account voice", 503)
        if self.current is not None:
            raise BridgeError("A component session or its cleanup is still active", 429)
        scope = self.bridge.open_read_scope(runtime_owned=True)
        with self.bridge.lock:
            self.bridge.session.expires = self.bridge.clock() + self.lifetime
        session = OwnedSession(scope, settings)
        self.current = session
        session.expiry_task = asyncio.create_task(self._expire(session))
        return {**scope, "provider": "elevenlabs", "voice": payload["voice"], "model": settings.tts_model,
                "transport": "smallwebrtc", "max_session_seconds": self.lifetime, "tool_support": "read-only",
                "runtime_verified": False, "provider_budget_enforced": False}

    def _authorized(self, payload, capability, *, allow_expired=False):
        session = self.current
        if (session is None or not isinstance(capability, str) or not capability.isascii() or len(capability) > 128
                or payload.get("session_id") != session.scope["session_id"]
                or not secrets.compare_digest(session.scope["capability"], capability)):
            raise BridgeError("Component session is not authorized", 401)
        if not allow_expired:
            with self.bridge.lock:
                self.bridge.authorize(payload, capability)
            if session.closing:
                raise BridgeError("Component cleanup must finish before reconnecting", 409)
        return session

    async def offer(self, payload: dict, capability: str) -> dict:
        fingerprint = validate_offer(payload)
        session = self._authorized(payload, capability)
        if session.fingerprint is not None and session.fingerprint != fingerprint:
            raise BridgeError("A component session cannot replace its original offer", 409)
        if session.offer_task is None:
            session.fingerprint = fingerprint
            session.offer_task = asyncio.create_task(self._connect(session, payload["sdp"]))
        try:
            return dict(await asyncio.shield(session.offer_task))
        except asyncio.CancelledError:
            raise
        except Exception:
            raise BridgeError("Component connection unavailable; Stop before reconnecting", 503) from None

    async def _connect(self, session, sdp):
        factory = self.factory
        if factory is None:
            from .component_webrtc import ComponentWebRTC

            factory = ComponentWebRTC
        session.runtime = factory(session.settings, self.bridge, session.scope,
                                  lambda: self._request_stop(session))
        async with asyncio.timeout(10):
            answer = await session.runtime.initialize(sdp)
        if session.closing or self.current is not session:
            raise BridgeError("Component connection ended during startup", 409)
        if (not isinstance(answer, dict) or answer.get("type") != "answer"
                or not isinstance(answer.get("sdp"), str) or len(answer["sdp"]) > 48 * 1024):
            raise BridgeError("Component runtime produced no bounded SDP answer", 503)
        return answer

    def _request_stop(self, session):
        if self.current is session and not session.closing:
            session.closing = True
            session.cleanup_task = asyncio.create_task(self._cleanup(session))
            # Consume failure without exposing provider exceptions; expiry keeps retry ownership.
            session.cleanup_task.add_done_callback(lambda task: None if task.cancelled() else task.exception())

    async def stop(self, payload: dict, capability: str) -> dict:
        if not isinstance(payload, dict) or set(payload) != {"session_id"}:
            raise BridgeError("Only the component session identity is accepted")
        cap_digest = hashlib.sha256(capability.encode("ascii")).hexdigest() if (
            isinstance(capability, str) and capability.isascii() and len(capability) <= 128
        ) else ""
        if self.current is None and self.last_stopped == (payload["session_id"], cap_digest):
            return {"ok": True, "duplicate": True, "runtime_closed": True}
        session = self._authorized(payload, capability, allow_expired=True)
        session.closing = True
        if session.cleanup_task is None or session.cleanup_task.done():
            session.cleanup_task = asyncio.create_task(self._cleanup(session))
            # A disconnected final waiter must not log raw resource exceptions.
            session.cleanup_task.add_done_callback(lambda task: None if task.cancelled() else task.exception())
        try:
            await asyncio.shield(session.cleanup_task)
        except asyncio.CancelledError:
            raise
        except Exception:
            raise BridgeError("Component cleanup incomplete; retry Stop before reconnecting", 503) from None
        return {"ok": True, "runtime_closed": True}

    async def _cleanup(self, session):
        if not session.revoked:
            with self.bridge.lock:
                if self.bridge.session and self.bridge.session.session_id == session.scope["session_id"]:
                    self.bridge.session.expires = 0
            session.revoked = True
        if session.offer_task is not None:
            if not session.offer_task.done():
                # Do not interrupt startup's own cancellation cleanup on every retry.
                if not session.offer_task.cancelling():
                    session.offer_task.cancel()
                _, pending = await asyncio.wait([session.offer_task], timeout=_STARTUP_CLOSE_SECONDS)
                if pending:
                    raise BridgeError("Component startup cleanup is incomplete; retry Stop", 503)
            # Consume a terminal startup error even when its HTTP waiter was cancelled.
            await asyncio.gather(session.offer_task, return_exceptions=True)
        if session.runtime is not None:
            async with asyncio.timeout(5):
                await session.runtime.close()
        self.bridge.release_runtime_scope(session.scope)
        self.last_stopped = (session.scope["session_id"],
                             hashlib.sha256(session.scope["capability"].encode("ascii")).hexdigest())
        self.current = None
        if not session.expiring and session.expiry_task is not asyncio.current_task():
            session.expiry_task.cancel()

    async def _expire(self, session):
        await asyncio.sleep(self.lifetime)
        session.expiring = True
        while self.current is session:
            try:
                await self.stop({"session_id": session.scope["session_id"]}, session.scope["capability"])
            except BridgeError:
                await asyncio.sleep(2)

    async def close(self):
        if self.current is not None:
            scope = self.current.scope
            await self.stop({"session_id": scope["session_id"]}, scope["capability"])
