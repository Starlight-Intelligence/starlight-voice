from __future__ import annotations

from time import perf_counter

from . import __version__
from .cognition import CognitionRouter, RouteTier


class AgentPipeline:
    """Text-first skeleton for the future Pipecat audio graph.

    Voice is an audio transport problem plus a reasoning/tool problem. This class
    gives the reasoning/tool side a stable contract before microphone, STT, and
    TTS adapters are wired.
    """

    def __init__(self) -> None:
        self.router = CognitionRouter()

    def health(self) -> dict[str, object]:
        from . import adapters

        availability = adapters.availability()
        # Cloud-first path: pipecat + groq STT lane + openrouter LLM + elevenlabs/cartesia TTS
        voice_ready = (
            availability.get("pipecat", False)
            and availability.get("openrouter", False)
            and (availability.get("elevenlabs", False) or availability.get("cartesia", False))
            and (availability.get("groq-openrouter", False) or availability.get("groq", False))
        )
        import importlib.util

        browser_live = importlib.util.find_spec("browser_use") is not None

        return {
            "service": "starlight-voice-sidecar",
            "version": __version__,
            "status": "ok",
            "capabilities": {
                "text_mode": True,
                "voice_loop": voice_ready,
                "browser_dry_run": True,
                "browser_live": browser_live,
                "doctor": True,
                "mcp": False,
                "dispatch": True,
                "proactive_brief": True,
            },
            "adapter_availability": availability,
        }

    def process_text(self, text: str) -> dict[str, object]:
        started = perf_counter()
        decision = self.router.decide(text)
        response = self._response_for(decision.tier, text)
        return {
            "input": text,
            "route": decision.to_dict(),
            "response": response,
            "elapsed_ms": int((perf_counter() - started) * 1000),
        }

    @staticmethod
    def _response_for(tier: RouteTier, text: str) -> dict[str, object]:
        if tier == RouteTier.CONTROL:
            return {"type": "control", "text": f"Control command acknowledged: {text}"}
        if tier == RouteTier.BROWSER:
            return {"type": "browser", "text": "Browser task routed. Use browser.task IPC to execute or dry-run."}
        if tier == RouteTier.CLI_AGENT:
            # Build a real handoff-packet (dry-run) so the response carries routing + approval tier.
            # Lazy import keeps `pipeline` import-safe where the dispatch deps (ulid) are absent.
            from .cognition.dispatch import Dispatcher

            outcome = Dispatcher(live=False).dispatch(text)
            packet = outcome["packet"]
            return {"type": "handoff", "text": packet["spoken_update_for_frank"], "dispatch": outcome}

        # FAST (default chat) and DELIBERATION now actually THINK: a grounded LLM answer
        # (memory recall + Frank-DNA voice + provider pin), degrade-first. Lazy import keeps
        # `pipeline` import-safe where the answer deps (httpx) are absent.
        from .cognition.answer import answer

        if tier == RouteTier.DELIBERATION:
            a = answer(text, fast=False)
            return {"type": "deliberation", "text": a["text"], "grounded": a["grounded"], "model": a["model"]}
        a = answer(text, fast=True)
        return {"type": "voice", "text": a["text"], "grounded": a["grounded"], "model": a["model"]}
