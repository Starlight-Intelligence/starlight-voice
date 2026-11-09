"""Legacy Pipecat experiments, separate from the native browser transports.

Headless graph assembly establishes dependency/configuration compatibility only.
Latency, naturalness, authentication, interruption and tool behavior need actual
runtime verification. Native browser configuration lives in native_bridge.py.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from . import adapters
from .config import Settings

_SYSTEM = (
    "You are Starlight, Frank's voice operator. Lead with the action. "
    "Reply in <=2 short sentences. No hedging, no preamble."
)


@dataclass(frozen=True)
class Variant:
    key: str
    label: str
    engine: str            # what replaces the middle of the graph
    key_required: str      # env var that must be live to test it
    tool_support: str      # how function-calling / dispatch works in this variant
    latency_hypothesis: str


VARIANTS: dict[str, Variant] = {
    "component": Variant(
        "component", "Component pipeline (A)", "Groq STT + OpenRouter LLM + ElevenLabs TTS",
        "OPENROUTER_API_KEY", "five scoped read tools through the existing broker; dispatch/approval unavailable",
        "component experiment; first playable audio and cost unmeasured",
    ),
    "openai-realtime": Variant(
        "openai-realtime", "OpenAI Realtime S2S (B)", "OpenAIRealtimeLLMService (one socket: STT+LLM+TTS+turn)",
        "OPENAI_API_KEY", "legacy Pipecat experiment; native browser read tools are separate",
        "speech-to-speech experiment; latency and preference unmeasured",
    ),
    "gemini-live": Variant(
        "gemini-live", "Gemini Live S2S (C)", "Gemini multimodal-live (optional Pipecat experiment)",
        "GEMINI_API_KEY", "tool registration not bound", "requires optional runtime and verified Google API access",
    ),
}


def _assemble(variant: str, settings: Settings, *, with_transport: bool):
    settings = settings or Settings.from_env()
    if variant == "component":
        from .voice_loop import build_graph
        pipeline, _ = build_graph(settings, with_transport=with_transport)
        return pipeline

    adapters.require("pipecat")
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.processors.aggregators.llm_context import LLMContext
    from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair

    from .voice_loop import _router_processor

    context = LLMContext(messages=[{"role": "system", "content": _SYSTEM}])
    pair = LLMContextAggregatorPair(context)

    if variant == "openai-realtime":
        from pipecat.services.openai.realtime.llm import OpenAIRealtimeLLMService
        s2s = OpenAIRealtimeLLMService(api_key=os.environ.get("OPENAI_API_KEY"))
    elif variant == "gemini-live":
        # not installed by default; surfaced so the bake-off reports it honestly
        from pipecat.services.gemini_multimodal_live.gemini import GeminiMultimodalLiveLLMService
        s2s = GeminiMultimodalLiveLLMService(api_key=os.environ.get("GEMINI_API_KEY"))
    else:
        raise ValueError(f"unknown variant '{variant}'")

    head, tail = [], []
    if with_transport:
        from pipecat.transports.local.audio import LocalAudioTransport, LocalAudioTransportParams
        t = LocalAudioTransport(LocalAudioTransportParams(audio_in_enabled=True, audio_out_enabled=True))
        head, tail = [t.input()], [t.output()]
    # S2S replaces STT+LLM+TTS; router still observes tier
    return Pipeline([*head, _router_processor(), pair.user(), s2s, *tail, pair.assistant()])


def selftest_variant(variant: str, settings: Settings | None = None) -> dict[str, object]:
    v = VARIANTS[variant]
    out: dict[str, object] = {
        "variant": variant, "engine": v.engine,
        "key_live": bool(os.environ.get(v.key_required)),
        "tool_support": v.tool_support, "latency_hypothesis": v.latency_hypothesis,
    }
    try:
        pipeline = _assemble(variant, settings or Settings.from_env(), with_transport=False)
        out["assembles"] = True
        out["processors"] = len(pipeline._processors) if hasattr(pipeline, "_processors") else None
    except Exception as e:
        out["assembles"] = False
        out["error"] = f"{type(e).__name__}: {str(e)[:120]}"
    return out


def selftest_all(settings: Settings | None = None) -> list[dict[str, object]]:
    """Assemble every variant headlessly; report which are runnable now. Honest about blocked lanes."""
    settings = settings or Settings.from_env()
    return [selftest_variant(k, settings) for k in VARIANTS]


async def run_variant(variant: str, settings: Settings | None = None) -> int:
    """Open mic/speakers and run a chosen architecture variant live (the bake-off on-device).

    Same harness as voice_loop.run, but the engine block is `variant`. Guarded so a fatal
    returns nonzero (Tauri restarts the sidecar). Needs the voice extra + a mic.
    """
    import sys

    from pipecat.pipeline.runner import PipelineRunner
    from pipecat.pipeline.task import PipelineParams, PipelineTask

    settings = settings or Settings.from_env()
    pipeline = _assemble(variant, settings, with_transport=True)
    task = PipelineTask(pipeline, params=PipelineParams(allow_interruptions=True))
    try:
        await PipelineRunner().run(task)
        return 0
    except Exception as e:  # pragma: no cover - needs a live mic + runner
        print(f"[voice:{variant}] fatal: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
