"""Actual SDK frames and lifecycle with synthetic REST; no models or providers."""
import asyncio

import pytest

pytest.importorskip("pipecat")

from pipecat.frames.frames import ErrorFrame, TranscriptionFrame  # noqa: E402

from starlight_voice.adapters.openrouter_transcription import TranscriptionError  # noqa: E402
from starlight_voice.adapters.openrouter_transcription_pipecat import create_openrouter_transcription_stt  # noqa: E402


@pytest.mark.parametrize("reply", ["Inspect current work", "", TranscriptionError("fixture")])
def test_real_sdk_receives_transcription_silence_or_redacted_fatal_error(reply):
    class Client:
        async def transcribe(self, audio):
            assert audio == b"fixture-WAV"
            if isinstance(reply, Exception):
                raise reply
            return reply

    async def run():
        service = create_openrouter_transcription_stt(api_key="fixture", model="x-ai/grok-stt-1.0", transcription=Client())
        try:
            frames = [frame async for frame in service.run_stt(b"fixture-WAV")]
            if isinstance(reply, Exception):
                assert len(frames) == 1 and isinstance(frames[0], ErrorFrame) and frames[0].fatal
                assert "fixture" not in frames[0].error
            elif reply:
                assert len(frames) == 1 and isinstance(frames[0], TranscriptionFrame)
                assert frames[0].text == reply
            else:
                assert frames == []
        finally:
            await service.cleanup()
    asyncio.run(run())


def test_grok_profile_pin_reasoning_and_key_reach_the_actual_sdk_without_requests(monkeypatch):
    pytest.importorskip("openai")
    from starlight_voice.config import Settings
    from starlight_voice.voice_loop import _llm_service

    monkeypatch.setenv("STARLIGHT_VOICE_PROFILE", "grok-openrouter")
    monkeypatch.setenv("STARLIGHT_VOICE_OPENROUTER_API_KEY", "synthetic-inference-fixture")
    monkeypatch.setenv("OPENROUTER_API_KEY", "wrong-shared-fixture")

    async def run():
        service = _llm_service(Settings.from_env())
        try:
            assert service._settings.model == "x-ai/grok-4.7"
            assert service._settings.extra["provider"] == {"order": ["xai"], "allow_fallbacks": False}
            assert service._settings.extra["reasoning"] == {"effort": "low"}
            assert service._client.api_key == "synthetic-inference-fixture"
            assert str(service._client.base_url) == "https://openrouter.ai/api/v1/"
        finally:
            await service._client.close()
            await service.cleanup()
    asyncio.run(run())


def test_grok_profile_audio_to_real_sdk_model_to_pcm_without_provider_network(monkeypatch):
    """Whole component graph with real adapters/SDK and in-memory HTTP only."""
    import base64
    import io
    import json
    import wave

    import httpx
    from openai import AsyncOpenAI
    from pipecat.frames.frames import EndFrame, InputAudioRawFrame, TTSAudioRawFrame
    from pipecat.pipeline.runner import PipelineRunner
    from pipecat.pipeline.worker import PipelineWorker
    from test_component_turns_pipecat import FixtureTransport, MarkerVAD, PassThrough
    from test_openrouter_speech import AudioStream

    from starlight_voice import voice_loop
    from starlight_voice.adapters.openrouter_speech import OpenRouterSpeech
    from starlight_voice.adapters.openrouter_speech_pipecat import create_openrouter_speech_tts
    from starlight_voice.adapters.openrouter_transcription import OpenRouterTranscription
    from starlight_voice.config import Settings

    monkeypatch.setenv("STARLIGHT_VOICE_PROFILE", "grok-openrouter")
    monkeypatch.setenv("STARLIGHT_VOICE_OPENROUTER_API_KEY", "synthetic-inference-fixture")
    monkeypatch.setenv("OPENROUTER_API_KEY", "wrong-shared-fixture")
    monkeypatch.setattr(voice_loop.adapters, "require", lambda engine: None)
    monkeypatch.setattr(voice_loop, "_live_vad_analyzer", MarkerVAD)

    async def run():
        requests, streams = [], []

        def response(request):
            assert request.headers["authorization"] == "Bearer synthetic-inference-fixture"
            assert request.url.host == "openrouter.ai"
            body = json.loads(request.content)
            requests.append((request.url.path, body))
            if request.url.path == "/api/v1/audio/transcriptions":
                assert body["model"] == "x-ai/grok-stt-1.0"
                with wave.open(io.BytesIO(base64.b64decode(body["input_audio"]["data"])), "rb") as segment:
                    assert segment.getframerate() == 16000 and segment.getnframes() > 0
                stream, mime = AudioStream([b'{"text":"Inspect current work"}']), "application/json"
            elif request.url.path == "/api/v1/chat/completions":
                assert body["model"] == "x-ai/grok-4.7"
                assert body["provider"] == {"order": ["xai"], "allow_fallbacks": False}
                assert body["reasoning"] == {"effort": "low"}
                assert any(message["role"] == "user" and "Inspect current work" in message["content"]
                           for message in body["messages"])
                assert {tool["function"]["name"] for tool in body["tools"]} == {
                    "portfolio_status", "recent_run_receipts", "node_health", "sis_recall_projection", "project_context",
                }
                chunks = [
                    {"id": "fixture", "object": "chat.completion.chunk", "model": "x-ai/grok-4.7",
                     "choices": [{"index": 0, "delta": {"role": "assistant", "content": "Current work is available."},
                                  "finish_reason": None}]},
                    {"id": "fixture", "object": "chat.completion.chunk", "model": "x-ai/grok-4.7",
                     "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
                ]
                stream = AudioStream([("data: " + json.dumps(chunk) + "\n\n").encode() for chunk in chunks]
                                     + [b"data: [DONE]\n\n"])
                mime = "text/event-stream"
            elif request.url.path == "/api/v1/audio/speech":
                assert body["model"] == "x-ai/grok-voice-tts-1.0" and body["voice"] == "ara"
                assert body["input"].strip() == "Current work is available."
                assert body["provider"]["options"]["xai"]["output_format"] == {"codec": "pcm", "sample_rate": 24000}
                stream, mime = AudioStream([b"\x01\x00" * 2400]), "audio/pcm"
            else:
                raise AssertionError("Unexpected HTTP request")
            streams.append(stream)
            return httpx.Response(200, headers={"content-type": mime}, stream=stream)

        settings = Settings.from_env()
        model = voice_loop._llm_service(settings)
        await model._client.close()
        model._client = AsyncOpenAI(api_key="synthetic-inference-fixture", base_url="https://openrouter.ai/api/v1",
                                   http_client=httpx.AsyncClient(transport=httpx.MockTransport(response)))
        stt = create_openrouter_transcription_stt(api_key="synthetic-inference-fixture", model=settings.stt_model,
            transcription=OpenRouterTranscription(api_key="synthetic-inference-fixture", model=settings.stt_model,
                                                  transport=httpx.MockTransport(response)))
        tts = create_openrouter_speech_tts(api_key="synthetic-inference-fixture", voice_id=settings.tts_voice_id,
            model=settings.tts_model, speech=OpenRouterSpeech(api_key="synthetic-inference-fixture",
                voice_id=settings.tts_voice_id, model=settings.tts_model, transport=httpx.MockTransport(response)))
        monkeypatch.setattr(voice_loop, "_stt_service", lambda settings: stt)
        monkeypatch.setattr(voice_loop, "_llm_service", lambda settings: model)
        monkeypatch.setattr(voice_loop, "_tts_service", lambda settings: tts)

        class AudioSink(PassThrough):
            def __init__(self):
                super().__init__()
                self.audio, self.received = [], asyncio.Event()

            async def process_frame(self, frame, direction):
                if isinstance(frame, TTSAudioRawFrame):
                    self.audio.append(frame)
                    self.received.set()
                await super().process_frame(frame, direction)

        transport = FixtureTransport({})
        transport.sink = AudioSink()
        pipeline, _ = voice_loop.build_graph(settings, with_transport=False, transport=transport)
        worker = PipelineWorker(pipeline, enable_rtvi=False)
        runner = asyncio.create_task(PipelineRunner(handle_sigint=False).run(worker))
        try:
            await asyncio.wait_for(transport.sink.started.wait(), 3)
            await worker.queue_frames([
                InputAudioRawFrame(b"\x01\x00" * 320, 16000, 1),
                InputAudioRawFrame(b"\x01\x00" * 320, 16000, 1),
                InputAudioRawFrame(b"\x00\x00" * 320, 16000, 1),
            ])
            await asyncio.wait_for(transport.sink.received.wait(), 5)
            await worker.queue_frames([EndFrame()])
            await asyncio.wait_for(runner, 3)
            assert [path for path, body in requests] == [
                "/api/v1/audio/transcriptions", "/api/v1/chat/completions", "/api/v1/audio/speech",
            ]
            assert b"".join(frame.audio for frame in transport.sink.audio) == b"\x01\x00" * 2400
            assert all(frame.sample_rate == 24000 and frame.num_channels == 1 for frame in transport.sink.audio)
            assert len(streams) == 3 and all(stream.closed for stream in streams)
        finally:
            if not runner.done():
                await worker.cancel()
                await asyncio.wait_for(runner, 3)
            await model._client.close()

    asyncio.run(asyncio.wait_for(run(), 15))
