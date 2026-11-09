"""Frame binding and provider cleanup without local devices or provider calls."""

import asyncio
import sys
import time
from types import SimpleNamespace

import pytest

from starlight_voice.adapters.eleven_dialogue import DialogueError, ElevenDialogue
from starlight_voice.adapters.eleven_dialogue_pipecat import create_dialogue_tts


@pytest.fixture
def frames(monkeypatch):
    class Audio:
        def __init__(self, audio, sample_rate, num_channels, *, context_id):
            self.audio, self.sample_rate, self.num_channels = audio, sample_rate, num_channels
            self.context_id = context_id

    class Error:
        def __init__(self, *, error, fatal=False):
            self.error = error
            self.fatal = fatal

    class Stop:
        def __init__(self, *, context_id):
            self.context_id = context_id

    class Service:
        def __init__(self, **kwargs):
            self.options = kwargs
            self.usage = []
            self.metrics_stopped = 0

        async def start_tts_usage_metrics(self, text):
            self.usage.append(text)

        async def stop_ttfb_metrics(self):
            self.metrics_stopped += 1

        async def on_audio_context_interrupted(self, context_id):
            self.interrupted = context_id

    monkeypatch.setitem(sys.modules, "pipecat.frames.frames",
                        SimpleNamespace(ErrorFrame=Error, TTSAudioRawFrame=Audio, TTSStoppedFrame=Stop))
    monkeypatch.setitem(sys.modules, "pipecat.services.settings", SimpleNamespace(TTSSettings=lambda **kw: kw))
    monkeypatch.setitem(sys.modules, "pipecat.services.tts_service",
                        SimpleNamespace(TTSService=Service, TextAggregationMode=SimpleNamespace(SENTENCE="sentence")))
    return SimpleNamespace(Audio=Audio, Error=Error)


def test_flush_closes_an_empty_context_instead_of_waiting_for_idle_timeout(frames):
    async def run():
        service = build(object())
        events = []
        service.audio_context_available = lambda context: context == "failed-turn"

        async def append(context, frame):
            events.append(("stop", context, frame.context_id))

        async def remove(context):
            events.append(("close", context))

        service.append_to_audio_context, service.remove_audio_context = append, remove
        await service.flush_audio("failed-turn")
        await service.flush_audio("absent")
        assert events == [("stop", "failed-turn", "failed-turn"), ("close", "failed-turn")]
    asyncio.run(run())


def build(dialogue):
    return create_dialogue_tts(api_key="test", voice_id="chosen", model="eleven_v4_turbo", dialogue=dialogue)


def test_pcm_frames_preserve_rate_channels_context_and_close_stream(frames):
    class Dialogue:
        closed = False

        async def stream(self, text):
            self.text = [part async for part in text]
            try:
                yield b"\x01\x00"
                yield b"\x02\x00"
            finally:
                self.closed = True

    async def run():
        dialogue = Dialogue()
        service = build(dialogue)
        result = [frame async for frame in service.run_tts("Hi.", "turn-id")]
        assert all(isinstance(frame, frames.Audio) for frame in result)
        assert [frame.audio for frame in result] == [b"\x01\x00", b"\x02\x00"]
        assert all((frame.sample_rate, frame.num_channels, frame.context_id) == (48000, 1, "turn-id")
                   for frame in result)
        assert dialogue.text == ["Hi."] and dialogue.closed
        assert service.options["push_start_frame"] and service.options["push_stop_frames"]
        assert service.options["stop_frame_timeout_s"] > 30
        assert service.usage == ["Hi."] and service._synthesis_task is None
    asyncio.run(run())


@pytest.mark.parametrize("silent", [False, True])
def test_failure_or_silence_emits_a_redacted_error_and_closes_stream(frames, silent):
    class Dialogue:
        closed = False

        async def stream(self, text):
            try:
                if not silent:
                    raise DialogueError("private-key-and-prompt")
                yield b""
            finally:
                self.closed = True

    async def run():
        dialogue = Dialogue()
        service = build(dialogue)
        result = [frame async for frame in service.run_tts("private text", "turn")]
        assert len(result) == 1 and isinstance(result[0], frames.Error)
        assert "private" not in result[0].error
        assert result[0].fatal
        assert dialogue.closed and service._synthesis_task is None
        assert service.metrics_stopped == 1
    asyncio.run(run())


def test_interruption_cancels_waiting_synthesis_and_closes_its_stream(frames):
    class Dialogue:
        def __init__(self):
            self.waiting = asyncio.Event()
            self.closed = False

        async def stream(self, text):
            try:
                self.waiting.set()
                await asyncio.Event().wait()
                yield b"unreachable"
            finally:
                self.closed = True

    async def run():
        dialogue = Dialogue()
        service = build(dialogue)

        async def collect():
            return [frame async for frame in service.run_tts("Hello.", "turn")]

        task = asyncio.create_task(collect())
        await dialogue.waiting.wait()
        await service.on_audio_context_interrupted("turn")
        assert task.cancelled() and dialogue.closed
        assert service.interrupted == "turn" and service._synthesis_task is None
        assert asyncio.all_tasks() == {asyncio.current_task()}
    asyncio.run(run())


def test_closing_frame_generator_closes_the_real_dialogue_socket(frames):
    from test_eleven_dialogue import Socket

    async def run():
        socket = Socket([{"audio": "AQA="}, {"is_final": True}])
        dialogue = ElevenDialogue(api_key="test", voice_id="chosen", connect=lambda *a, **k: socket)
        service = build(dialogue)
        stream = service.run_tts("Hi.", "turn")
        assert (await anext(stream)).audio == b"\x01\x00"
        await stream.aclose()
        assert socket.closed
        assert asyncio.all_tasks() == {asyncio.current_task()}
    asyncio.run(run())


@pytest.mark.parametrize("mode", ["success", "error", "silent"])
@pytest.mark.parametrize("engine", ["dialogue", "openrouter-speech"])
def test_actual_pipecat_pipeline_orders_audio_and_ends_on_failure_without_devices(mode, engine):
    pytest.importorskip("pipecat")
    from pipecat.frames.frames import EndFrame, TTSAudioRawFrame, TTSSpeakFrame, TTSStartedFrame, TTSStoppedFrame
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.runner import PipelineRunner
    from pipecat.pipeline.worker import PipelineWorker
    from pipecat.processors.frame_processor import FrameProcessor

    from starlight_voice.adapters.openrouter_speech import SpeechError
    from starlight_voice.adapters.openrouter_speech_pipecat import create_openrouter_speech_tts

    error_class = DialogueError if engine == "dialogue" else SpeechError
    sample_rate = 48000 if engine == "dialogue" else 24000

    class Dialogue:
        async def stream(self, text):
            assert [part async for part in text] == ["Hello."]
            if mode == "error":
                raise error_class("private-detail")
            if mode == "success":
                yield b"\x01\x00" * 960

    class Sink(FrameProcessor):
        def __init__(self):
            super().__init__()
            self.frames = []

        async def process_frame(self, frame, direction):
            await super().process_frame(frame, direction)
            self.frames.append(frame)
            await self.push_frame(frame, direction)

    async def run():
        service = build(Dialogue()) if engine == "dialogue" else create_openrouter_speech_tts(
            api_key="test", voice_id="george", model="elevenlabs/eleven-v4-turbo", speech=Dialogue())
        sink = Sink()
        errors = []
        task = PipelineWorker(Pipeline([service, sink]), enable_rtvi=False)

        @task.event_handler("on_pipeline_error")
        async def on_error(worker, frame):
            errors.append(frame)

        await task.queue_frames([TTSSpeakFrame("Hello."), EndFrame()])
        started = time.monotonic()
        await asyncio.wait_for(PipelineRunner(handle_sigint=False).run(task), timeout=10)
        assert time.monotonic() - started < 3, "Synthesis shutdown must not depend on the audio context idle timer"
        relevant = [frame for frame in sink.frames
                    if isinstance(frame, (TTSStartedFrame, TTSAudioRawFrame, TTSStoppedFrame))]
        if mode == "success":
            assert [type(frame) for frame in relevant] == [TTSStartedFrame, TTSAudioRawFrame, TTSStoppedFrame]
            assert relevant[1].sample_rate == sample_rate and relevant[1].num_channels == 1
            assert len({frame.context_id for frame in relevant}) == 1
            assert not errors
        else:
            assert errors and errors[0].fatal and "private" not in errors[0].error
            assert not any(isinstance(frame, TTSAudioRawFrame) for frame in relevant)
    asyncio.run(run())
