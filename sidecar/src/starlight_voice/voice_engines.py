"""Architecture bake-off — swap the engine block, keep the harness.

The key realization (verified against pipecat 1.3.0): "component pipeline" vs "speech-to-speech
realtime" is NOT an either/or. Both run in the same Pipecat graph; you swap the middle:

  Component (A):  transport.in -> STT -> [router] -> LLM -> TTS -> transport.out
  OpenAI S2S (B): transport.in -> [router] -> OpenAIRealtimeLLMService(S2S) -> transport.out
  Gemini S2S (C): same shape, Gemini multimodal-live (key-blocked: GEMINI slot holds an
                  OpenRouter-format key per machine memory)

This module assembles each variant for empirical comparison (Model-Arena style): measure
first-audio latency, naturalness, tool/dispatch capability, barge-in, cost on-device. Variant A
delegates to voice_loop (no duplication); B/C are built here. selftest_all() reports which
variants assemble right now without guessing.
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
        "OPENROUTER_API_KEY", "native (router FrameProcessor + LLM tool-calls)",
        "swappable; ~800ms-tight on 1650; provider-pin dependent",
    ),
    "openai-realtime": Variant(
        "openai-realtime", "OpenAI Realtime S2S (B)", "OpenAIRealtimeLLMService (one socket: STT+LLM+TTS+turn)",
        "OPENAI_API_KEY", "realtime function-calling (router still rides as FrameProcessor)",
        "lowest latency + most natural; harder to inject mid-turn tool/approval logic",
    ),
    "gemini-live": Variant(
        "gemini-live", "Gemini Live S2S (C)", "Gemini multimodal-live (NOT installed / key-blocked)",
        "GEMINI_API_KEY", "realtime function-calling", "S2S; blocked until real Google key + extra",
    ),
    "x-voice": Variant(
        "x-voice", "xAI Grok Voice (X)", "grok-voice-latest speech-to-speech, double-clap gate",
        "XAI_API_KEY", "client function meta_harness -> tools/dispatch-route.mjs, no spawn",
        "sub-second first audio on the xAI socket; meter runs only while the session is open",
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

    from .voice_loop import _local_audio_transport, _router_processor

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
        t = _local_audio_transport()
        head, tail = [t.input()], [t.output()]
    # S2S replaces STT+LLM+TTS; router still observes tier
    return Pipeline([*head, _router_processor(), pair.user(), s2s, *tail, pair.assistant()])


def selftest_variant(variant: str, settings: Settings | None = None) -> dict[str, object]:
    v = VARIANTS[variant]
    if variant == "x-voice":
        from .x_voice import readiness

        report = readiness()
        return {
            "variant": variant,
            "engine": v.engine,
            "key_live": report["token"],
            "tool_support": v.tool_support,
            "latency_hypothesis": v.latency_hypothesis,
            "assembles": bool(report["clap_ok"] and report["dispatch_script"]),
            "ready": report["ready"],
            "token_source": report["token_source"],
            "missing": report["missing"],
        }
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
    x-voice is the clap-gated Grok Voice path and does not use Pipecat.
    """
    if variant == "x-voice":
        from .x_voice import run_clap_loop

        return await run_clap_loop()

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
