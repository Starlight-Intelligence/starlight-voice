"""PCM rate/context and interruption across the actual adapter/binding boundary."""

import asyncio

import httpx
import pytest
import test_eleven_dialogue_pipecat as dialogue_tests
from test_openrouter_speech import AudioStream

from starlight_voice.adapters.openrouter_speech import OpenRouterSpeech, SpeechError
from starlight_voice.adapters.openrouter_speech_pipecat import create_openrouter_speech_tts


@pytest.fixture
def frames(monkeypatch):
    return dialogue_tests.frames.__wrapped__(monkeypatch)


def build(speech):
    return create_openrouter_speech_tts(api_key="test", voice_id="george",
                                       model="elevenlabs/eleven-v4-turbo", speech=speech)


def test_actual_rest_audio_becomes_24khz_mono_frames_and_closure_releases_http(frames):
    async def run():
        response = AudioStream([b"\x01\x00"], stall=True)
        speech = OpenRouterSpeech(api_key="test", voice_id="george", model="elevenlabs/eleven-v4-turbo",
                                  transport=httpx.MockTransport(lambda req: httpx.Response(
                                      200, headers={"content-type": "audio/pcm"}, stream=response)))
        service = build(speech)
        audio = service.run_tts("Hello.", "turn")
        frame = await anext(audio)
        assert (frame.audio, frame.sample_rate, frame.num_channels, frame.context_id) == (b"\x01\x00", 24000, 1, "turn")
        await audio.aclose()
        assert response.closed and service._synthesis_task is None
    asyncio.run(run())


def test_interruption_cancels_actual_rest_response_without_orphan_tasks(frames):
    async def run():
        response = AudioStream(stall=True)
        speech = OpenRouterSpeech(api_key="test", voice_id="george", model="elevenlabs/eleven-v4-turbo",
                                  transport=httpx.MockTransport(lambda req: httpx.Response(
                                      200, headers={"content-type": "audio/pcm"}, stream=response)))
        service = build(speech)

        async def collect():
            return [frame async for frame in service.run_tts("Hello.", "turn")]

        task = asyncio.create_task(collect())
        await response.entered.wait()
        await service.on_audio_context_interrupted("turn")
        assert task.cancelled() and response.closed
        assert service.interrupted == "turn" and service._synthesis_task is None
        assert asyncio.all_tasks() == {asyncio.current_task()}
    asyncio.run(run())


@pytest.mark.parametrize("silent", [False, True])
def test_error_or_silence_is_fatal_and_redacted(frames, silent):
    class Speech:
        async def stream(self, text):
            if not silent:
                raise SpeechError("private detail")
            yield b""

    async def run():
        service = build(Speech())
        result = [frame async for frame in service.run_tts("Hello.", "turn")]
        assert len(result) == 1 and isinstance(result[0], frames.Error)
        assert result[0].fatal and "private" not in result[0].error
        assert service._synthesis_task is None
    asyncio.run(run())


def test_failed_context_flush_removes_its_queue_without_an_idle_wait(frames):
    async def run():
        service = build(object())
        events = []
        service.audio_context_available = lambda context: context == "failed"

        async def append(context, frame):
            events.append(("stop", frame.context_id))

        async def remove(context):
            events.append(("remove", context))

        service.append_to_audio_context, service.remove_audio_context = append, remove
        await service.flush_audio("failed")
        await service.flush_audio("absent")
        assert events == [("stop", "failed"), ("remove", "failed")]
    asyncio.run(run())
