"""Actual SDK VAD -> segmented STT -> context flow, without models/devices/providers."""

import asyncio
import io
import sys
import wave
from types import SimpleNamespace

import pytest

pytest.importorskip("pipecat")

from pipecat.audio.vad.vad_analyzer import VADAnalyzer, VADState  # noqa: E402
from pipecat.frames.frames import (  # noqa: E402
    EndFrame,
    InputAudioRawFrame,
    LLMContextFrame,
    StartFrame,
    TranscriptionFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.pipeline.runner import PipelineRunner  # noqa: E402
from pipecat.pipeline.worker import PipelineWorker  # noqa: E402
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor  # noqa: E402
from pipecat.services.llm_service import LLMService  # noqa: E402
from pipecat.services.stt_service import SegmentedSTTService  # noqa: E402

from starlight_voice import voice_loop  # noqa: E402
from starlight_voice.config import Settings  # noqa: E402


class MarkerVAD(VADAnalyzer):
    """Deterministic state fixture; establishes frame wiring, not speech accuracy."""

    def num_frames_required(self):
        return 320

    def voice_confidence(self, buffer):
        return 1.0 if any(buffer) else 0.0

    async def analyze_audio(self, buffer):
        return VADState.SPEAKING if any(buffer) else VADState.QUIET


class RecordingSTT(SegmentedSTTService):
    def __init__(self):
        super().__init__()
        self.segments = []
        self.boundaries = []

    async def process_frame(self, frame, direction):
        if isinstance(frame, (VADUserStartedSpeakingFrame, VADUserStoppedSpeakingFrame)):
            self.boundaries.append(type(frame).__name__)
        await super().process_frame(frame, direction)

    async def run_stt(self, audio):
        with wave.open(io.BytesIO(audio), "rb") as segment:
            assert segment.getframerate() == 16000
            assert segment.getnchannels() == 1 and segment.getsampwidth() == 2
            assert segment.getnframes() > 0
        self.segments.append(audio)
        yield TranscriptionFrame("Inspect current work", "fixture", "2026-10-11T00:00:00.000+00:00")


class RecordingModel(LLMService):
    def __init__(self):
        super().__init__()
        self.contexts = []
        self.reply_ready = asyncio.Event()

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMContextFrame) and direction == FrameDirection.DOWNSTREAM:
            self.contexts.append(frame.context.get_messages().copy())
            self.reply_ready.set()
        else:
            await self.push_frame(frame, direction)


class PassThrough(FrameProcessor):
    def __init__(self):
        super().__init__()
        self.started = asyncio.Event()

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if isinstance(frame, StartFrame):
            self.started.set()
        await self.push_frame(frame, direction)


class FixtureTransport:
    def __init__(self, params):
        self.source, self.sink = PassThrough(), PassThrough()

    def input(self):
        return self.source

    def output(self):
        return self.sink


def patch_services(monkeypatch, stt, model):
    monkeypatch.setattr(voice_loop.adapters, "require", lambda engine: None)
    monkeypatch.setattr(voice_loop, "_stt_service", lambda settings: stt)
    monkeypatch.setattr(voice_loop, "_llm_service", lambda settings: model)
    monkeypatch.setattr(voice_loop, "_tts_service", lambda settings: PassThrough())
    monkeypatch.setitem(sys.modules, "pipecat.transports.local.audio", SimpleNamespace(
        LocalAudioTransport=FixtureTransport, LocalAudioTransportParams=lambda **params: params,
    ))


def test_component_audio_reaches_segmented_transcription_and_model_context(monkeypatch):
    async def run():
        stt, model = RecordingSTT(), RecordingModel()
        patch_services(monkeypatch, stt, model)
        loaded = []

        def vad_factory():
            loaded.append(True)
            return MarkerVAD()

        # raising=False lets the regression exercise the pre-fix graph, which ignores this factory.
        monkeypatch.setattr(voice_loop, "_live_vad_analyzer", vad_factory, raising=False)
        pipeline, transport = voice_loop.build_graph(Settings(), with_transport=True)
        assert loaded == [True], "Local graph must bind VAD before segmented STT can produce turns"
        worker = PipelineWorker(pipeline, enable_rtvi=False)
        runner = asyncio.create_task(PipelineRunner(handle_sigint=False).run(worker))
        try:
            await asyncio.wait_for(transport.sink.started.wait(), 3)
            # Separate speech/silence blocks traverse actual SDK processors; no microphone or model.
            await worker.queue_frames([
                InputAudioRawFrame(b"\x01\x00" * 320, 16000, 1),
                InputAudioRawFrame(b"\x01\x00" * 320, 16000, 1),
                InputAudioRawFrame(b"\x00\x00" * 320, 16000, 1),
            ])
            await asyncio.wait_for(model.reply_ready.wait(), 4)
            assert len(stt.segments) == 1
            assert "VADUserStartedSpeakingFrame" in stt.boundaries
            assert "VADUserStoppedSpeakingFrame" in stt.boundaries
            assert any(message.get("role") == "user" and "Inspect current work" in message.get("content", "")
                       for context in model.contexts for message in context)
            await worker.queue_frames([EndFrame()])
            await asyncio.wait_for(runner, 3)
        finally:
            if not runner.done():
                await worker.cancel()
                await asyncio.wait_for(runner, 3)

    asyncio.run(run())


def test_headless_graph_never_loads_voice_detection_or_audio_devices(monkeypatch):
    async def run():
        stt, model = RecordingSTT(), RecordingModel()
        patch_services(monkeypatch, stt, model)

        def forbidden():
            raise AssertionError("Headless assembly cannot load voice detection")

        monkeypatch.setattr(voice_loop, "_live_vad_analyzer", forbidden, raising=False)
        pipeline, transport = voice_loop.build_graph(Settings(), with_transport=False)
        assert transport is None
        assert stt in pipeline._processors and model in pipeline._processors

    asyncio.run(run())
