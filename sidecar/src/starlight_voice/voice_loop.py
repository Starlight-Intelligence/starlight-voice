"""In-process cloud-first realtime voice loop (Pipecat 1.3.0).

Cloud-first per the 2026-06-15 decision: mic -> Groq STT -> OpenRouter LLM -> ElevenLabs TTS
-> speakers, orchestrated by Pipecat entirely inside this Python sidecar. The Rust/Tauri
shell owns tray + lifecycle; audio never crosses the stdio IPC boundary. The local
component conversation needs explicit voice detection to segment Groq transcription.

The `CognitionRouter` rides the graph as a FrameProcessor (observes tier on each final
transcript) — it classifies, it does NOT call the LLM or pick the engine (that stays in
`config.Settings` / the LLM service), keeping the router's frozen contract intact.

Pipecat is imported lazily so importing this module is cheap and safe on a machine without
the `voice` extra installed (`adapters.require()` gives the actionable install error).
API verified live against pipecat-ai 1.3.0 (service/transport/context-aggregator names).
"""

from __future__ import annotations

import asyncio
import os
import sys

from . import adapters
from .cognition import CognitionRouter
from .config import Settings, openrouter_inference_key


def _stt_service(settings: Settings):
    """Cloud STT. Default Groq Whisper (live GROQ_API_KEY)."""
    if settings.stt_engine == "openrouter-transcription":
        from .adapters.openrouter_transcription_pipecat import create_openrouter_transcription_stt

        return create_openrouter_transcription_stt(api_key=openrouter_inference_key(), model=settings.stt_model)
    if settings.stt_engine != "groq-openrouter":
        raise adapters.VoiceDepsUnavailable(f"STT engine '{settings.stt_engine}' is not implemented in this graph")
    from pipecat.services.groq.stt import GroqSTTService

    return GroqSTTService(
        api_key=os.environ.get("GROQ_API_KEY"),
        model=settings.stt_model or "whisper-large-v3-turbo",
    )


def _llm_service(settings: Settings):
    """LLM via the OpenRouter gateway, with the FAST-tier provider PINNED.

    Provider-pin-by-tier (Cerebras for the FAST/voice tier) is owned here, NOT the router —
    without it OpenRouter may route to a 600ms+ TTFT provider and silently break the SLA.

    Uses the canonical `settings=` API (the old `params=`/InputParams path silently DROPPED
    the pin — review wf_a9484479 reproduced svc._settings.extra == {}). allow_fallbacks=False:
    the voice tier must NOT egress transcripts to an arbitrary slow upstream on a near-miss.
    Fails LOUD at build if the pin didn't land — never ship the SLA feature as a silent no-op.
    """
    api_key = openrouter_inference_key()
    if not api_key:
        raise adapters.VoiceDepsUnavailable("OpenRouter text needs a normal inference key")
    if settings.llm_model.startswith("x-ai/") and settings.llm_fast_provider != "xai":
        raise adapters.VoiceDepsUnavailable("Grok text requires the xai provider pin")
    if settings.llm_reasoning_effort not in {"", "none", "low", "medium", "high", "xhigh"}:
        raise adapters.VoiceDepsUnavailable("Select a supported explicit reasoning effort")
    from pipecat.services.openrouter.llm import OpenRouterLLMService

    provider = {"order": [settings.llm_fast_provider], "allow_fallbacks": False}
    body = {"provider": provider}
    if settings.llm_reasoning_effort:
        body["reasoning"] = {"effort": settings.llm_reasoning_effort}
    # Pipecat expands Settings.extra as OpenAI client keyword arguments.
    # OpenRouter-specific JSON belongs in extra_body, not in those keywords.
    extra = {"extra_body": body}
    svc = OpenRouterLLMService(
        api_key=api_key,
        settings=OpenRouterLLMService.Settings(model=settings.llm_model, extra=extra),
    )
    landed = getattr(getattr(svc, "_settings", None), "extra", None) or {}
    if landed.get("extra_body", {}).get("provider") != provider:
        raise RuntimeError(
            "OpenRouter provider-pin DROPPED (svc._settings.extra_body has no exact provider pin) — "
            "FAST-tier TTFT SLA would silently break. Check the pipecat Settings API."
        )
    return svc


def _tts_service(settings: Settings):
    """Select the provider protocol before constructing an optional Pipecat service."""
    if settings.tts_engine == "openrouter-speech":
        from .adapters.openrouter_speech import OpenRouterSpeech

        if settings.tts_model not in OpenRouterSpeech.MODELS:
            raise adapters.VoiceDepsUnavailable("Select an explicit supported OpenRouter speech model")
        api_key = openrouter_inference_key()
        if not api_key.strip() or api_key.strip() == "*not found*" or not settings.tts_voice_id.strip():
            raise adapters.VoiceDepsUnavailable(
                "OpenRouter speech needs an inference OPENROUTER_API_KEY and explicit STARLIGHT_TTS_VOICE_ID"
            )
        from .adapters.openrouter_speech_pipecat import create_openrouter_speech_tts

        return create_openrouter_speech_tts(api_key=api_key, voice_id=settings.tts_voice_id,
                                           model=settings.tts_model)
    if settings.tts_engine != "elevenlabs":
        raise adapters.VoiceDepsUnavailable(f"TTS engine '{settings.tts_engine}' is not implemented in this graph")
    if settings.tts_model in {"eleven_v4_turbo", "eleven_v3_conversational"}:
        api_key = os.environ.get("ELEVENLABS_API_KEY", "")
        if not api_key.strip() or not settings.tts_voice_id.strip():
            raise adapters.VoiceDepsUnavailable(
                "Dialogue TTS needs ELEVENLABS_API_KEY and an explicit STARLIGHT_TTS_VOICE_ID"
            )
        from .adapters.eleven_dialogue_pipecat import create_dialogue_tts

        return create_dialogue_tts(api_key=api_key, voice_id=settings.tts_voice_id,
                                   model=settings.tts_model)
    if settings.tts_model not in {"eleven_flash_v2_5", "eleven_flash_v2", "eleven_multilingual_v2"}:
        raise adapters.VoiceDepsUnavailable(
            f"TTS model '{settings.tts_model}' is not implemented in this graph"
        )
    from pipecat.services.elevenlabs.tts import ElevenLabsTTSService

    kwargs = {"api_key": os.environ.get("ELEVENLABS_API_KEY"), "model": settings.tts_model}
    if settings.tts_voice_id:
        kwargs["voice_id"] = settings.tts_voice_id
    return ElevenLabsTTSService(**kwargs)


def _router_processor():
    """A FrameProcessor that tags each final transcript with its CognitionRouter tier.

    Observes only — gating per tier (dispatch / approval) is a later phase. Keeps the
    router's frozen `decide(text) -> RouteDecision` contract unchanged.
    """
    from pipecat.frames.frames import TranscriptionFrame
    from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

    router = CognitionRouter()

    class RouterProcessor(FrameProcessor):
        async def process_frame(self, frame, direction: FrameDirection):
            await super().process_frame(frame, direction)
            if isinstance(frame, TranscriptionFrame) and getattr(frame, "text", "").strip():
                decision = router.decide(frame.text)
                # surfaced for telemetry / future per-tier gating; non-blocking
                frame.metadata = {**getattr(frame, "metadata", {}), "route_tier": decision.tier.value}
            await self.push_frame(frame, direction)

    return RouterProcessor()


def _live_vad_analyzer():
    """Load voice detection only for an explicitly started local component session."""
    try:
        from pipecat.audio.vad.silero import SileroVADAnalyzer
    except ImportError:
        raise adapters.VoiceDepsUnavailable(
            "Component microphone conversation needs optional Silero voice detection. "
            "Install: uv pip install -e .[voice,providers]. "
            "Native browser conversation does not need this extra."
        ) from None
    return SileroVADAnalyzer()


def build_graph(settings: Settings | None = None, *, with_transport: bool,
                transport=None, read_bridge=None, read_scope=None, reply_observer=None, own_resource=None):
    """Assemble the Pipecat pipeline.

    with_transport=False builds the service chain only (no mic/speaker) so the graph can be
    validated headlessly. with_transport=True binds local audio and optional Silero VAD.
    Groq's segmented STT requires VAD events; graph assembly alone is insufficient.
    Returns (pipeline, transport_or_None).
    """
    if with_transport and transport is not None:
        raise ValueError("Select local audio or an existing transport, not both")
    if read_scope is not None and read_bridge is None:
        raise ValueError("An external read scope needs its existing broker")
    settings = settings or Settings.from_env()
    audio_enabled = with_transport or transport is not None
    # Fail early (with an actionable install hint) if a SELECTED engine's deps are missing —
    # replaces a former dead no-op loop. require() raises VoiceDepsUnavailable on absence.
    for engine in (settings.stt_engine, "openrouter", settings.tts_engine, "pipecat"):
        adapters.require(engine)

    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.processors.aggregators.llm_context import LLMContext
    from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair

    from .component_tools import READ_INSTRUCTIONS, bind_component_tools

    def owned(resource):
        if own_resource is not None and resource is not None:
            own_resource(resource)
        return resource

    vad = owned(_live_vad_analyzer()) if audio_enabled else None
    stt = owned(_stt_service(settings))
    llm = owned(_llm_service(settings))
    tts = owned(_tts_service(settings))
    router_proc = _router_processor()
    tools, tool_lifecycle = (bind_component_tools(llm, bridge=read_bridge, scope=read_scope)
                             if read_bridge is not None else bind_component_tools(llm))

    context = LLMContext(
        messages=[{
            "role": "system",
            "content": READ_INSTRUCTIONS,
        }], tools=tools,
    )
    if audio_enabled:
        from pipecat.processors.aggregators.llm_response_universal import LLMUserAggregatorParams

        pair = LLMContextAggregatorPair(context, user_params=LLMUserAggregatorParams(vad_analyzer=vad))
    else:
        pair = LLMContextAggregatorPair(context)

    head: list = []
    tail: list = []
    if with_transport:
        from pipecat.transports.local.audio import LocalAudioTransport, LocalAudioTransportParams

        transport = LocalAudioTransport(LocalAudioTransportParams(audio_in_enabled=True, audio_out_enabled=True))
    if transport is not None:
        head = [transport.input()]
        tail = [transport.output()]

    pipeline = Pipeline(
        [*head, stt, router_proc, tool_lifecycle, pair.user(), llm,
         *([reply_observer] if reply_observer is not None else []), tts, *tail, pair.assistant()]
    )
    return pipeline, transport


def selftest(settings: Settings | None = None) -> dict[str, object]:
    """Construct the full service chain + pipeline WITHOUT opening audio. Headless-safe.

    Catches ctor/import/assembly errors (wrong arg names, moved modules) before a live run.
    Does NOT prove latency — that needs the on-device run with a mic (benchmarks first-audio).
    """
    settings = settings or Settings.from_env()
    pipeline, _ = build_graph(settings, with_transport=False)
    return {
        "ok": True,
        "pipeline_processors": len(pipeline._processors) if hasattr(pipeline, "_processors") else None,
        "stt": settings.stt_engine,
        "llm": f"{settings.llm_model}@{settings.llm_fast_provider}",
        "tts": settings.tts_engine,
    }


async def run(settings: Settings | None = None) -> int:
    """Live run: open mic/speakers and run the loop until EndFrame. Needs the `voice` extra + a mic.

    Resilience (review wf_a9484479): a cloud loop over 3 networked providers WILL hit transient
    429/500/socket-drops on a mobile link — that's the common case. The runner is guarded so a
    fatal returns a nonzero exit (the Tauri shell restarts the sidecar) instead of crashing
    silently. Per-turn retry / spoken "lost the model, retrying" fallback is wired on-device where
    the live runner can be exercised (the pipecat error-event name must be confirmed against a run).
    """
    from pipecat.pipeline.runner import PipelineRunner
    from pipecat.pipeline.task import PipelineParams, PipelineTask

    settings = settings or Settings.from_env()
    pipeline, _ = build_graph(settings, with_transport=True)
    task = PipelineTask(pipeline, params=PipelineParams(allow_interruptions=True))
    async def failed_cleanup():
        # SDK setup can fail before its normal worker teardown begins. Attempt
        # processor cleanup without turning a fatal exit into an unbounded wait.
        try:
            async with asyncio.timeout(5):
                await pipeline.cleanup()
        except Exception:
            print("[voice] pipeline cleanup incomplete; stop this process before restarting", file=sys.stderr)

    try:
        await PipelineRunner().run(task)
        return 0
    except asyncio.CancelledError:
        await failed_cleanup()
        raise
    except Exception:
        # Provider exceptions can include credentials, transcripts or URLs.
        print("[voice] fatal pipeline error; check provider setup and device access", file=sys.stderr)
        await failed_cleanup()
        return 1
