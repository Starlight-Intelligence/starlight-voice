"""Bounded REST WAV transcription. Construction never opens IO or imports models."""
from __future__ import annotations

import asyncio
import base64
import io
import json
import math
import wave

from ..http_transport import verified_context


class TranscriptionError(RuntimeError):
    """Redacted provider failure; audio, transcript and provider bodies stay private."""


class OpenRouterTranscription:
    MODEL = "x-ai/grok-stt-1.0"
    MAX_AUDIO_BYTES = 1024 * 1024
    MAX_RESPONSE_BYTES = 64 * 1024
    MAX_TEXT_CHARACTERS = 8192

    def __init__(self, *, api_key: str, model: str, timeout_s: float = 30, transport=None):
        if not isinstance(api_key, str) or not api_key.strip() or api_key.strip() == "*not found*":
            raise ValueError("An OpenRouter inference API key is required")
        if model != self.MODEL:
            raise ValueError("Select the supported Grok transcription model")
        if not math.isfinite(timeout_s) or not 0 < timeout_s <= 120:
            raise ValueError("Transcription timeout must be between 0 and 120 seconds")
        self._api_key, self.model = api_key, model
        self.timeout_s, self._transport = timeout_s, transport

    async def transcribe(self, audio: bytes) -> str:
        try:
            if not isinstance(audio, bytes) or not 44 <= len(audio) <= self.MAX_AUDIO_BYTES:
                raise TranscriptionError("Transcription audio exceeds the segment allowance or is empty")
            with wave.open(io.BytesIO(audio), "rb") as wav:
                if (wav.getnchannels() != 1 or wav.getsampwidth() != 2 or wav.getcomptype() != "NONE"
                        or wav.getframerate() not in {16000, 24000, 48000}
                        or not 0 < wav.getnframes() <= 30 * wav.getframerate()):
                    raise TranscriptionError("Transcription needs a bounded mono PCM16 WAV segment")
                if len(wav.readframes(wav.getnframes())) != wav.getnframes() * 2:
                    raise TranscriptionError("Transcription WAV segment is incomplete")
            import httpx

            async with asyncio.timeout(self.timeout_s), httpx.AsyncClient(
                trust_env=False, follow_redirects=False, timeout=min(self.timeout_s, 5),
                transport=self._transport,
                verify=verified_context() if self._transport is None else True,
            ) as client:
                async with client.stream(
                    "POST", "https://openrouter.ai/api/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {self._api_key}", "Accept-Encoding": "identity"},
                    json={"model": self.model, "input_audio": {
                        "data": base64.b64encode(audio).decode("ascii"), "format": "wav",
                    }, "response_format": "json"},
                ) as response:
                    if response.status_code != 200:
                        raise TranscriptionError("OpenRouter rejected transcription")
                    if (response.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json"
                            or response.headers.get("content-encoding", "identity").lower() != "identity"):
                        raise TranscriptionError("Transcription response is not uncompressed JSON")
                    body = bytearray()
                    async for chunk in response.aiter_raw():
                        if len(body) + len(chunk) > self.MAX_RESPONSE_BYTES:
                            raise TranscriptionError("Transcription response exceeds the allowance")
                        body.extend(chunk)
                    payload = json.loads(body)
                    text = payload.get("text") if isinstance(payload, dict) else None
                    if not isinstance(text, str) or len(text) > self.MAX_TEXT_CHARACTERS:
                        raise TranscriptionError("Transcription response has no bounded text")
                    # Empty text is valid for silence; it must not trigger an invented utterance.
                    return text.strip()
        except TranscriptionError:
            raise
        except Exception:
            raise TranscriptionError("OpenRouter transcription failed or timed out") from None
