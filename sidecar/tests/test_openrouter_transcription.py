"""Real adapter and HTTP fixture; no account inference or audio device."""
import asyncio
import base64
import io
import json
import wave

import httpx
import pytest
from test_openrouter_speech import AudioStream

from starlight_voice.adapters.openrouter_transcription import OpenRouterTranscription, TranscriptionError


def wav(seconds=0.01):
    result = io.BytesIO()
    with wave.open(result, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(b"\x00\x00" * int(seconds * 16000))
    return result.getvalue()


def build(handler, **kwargs):
    return OpenRouterTranscription(api_key="fixture", model=OpenRouterTranscription.MODEL,
                                   transport=httpx.MockTransport(handler), **kwargs)


def test_exact_bounded_request_returns_text_and_closes_response():
    stream = AudioStream([b'{"text":"', b'Inspect current work"}'])
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, headers={"content-type": "application/json"}, stream=stream)

    assert asyncio.run(build(handler).transcribe(wav())) == "Inspect current work"
    assert stream.closed and len(requests) == 1
    assert str(requests[0].url) == "https://openrouter.ai/api/v1/audio/transcriptions"
    body = json.loads(requests[0].content)
    assert body == {"model": "x-ai/grok-stt-1.0", "input_audio": {
        "data": base64.b64encode(wav()).decode(), "format": "wav"}, "response_format": "json"}


@pytest.mark.parametrize("audio", [b"", b"x" * 44, wav(31), wav()[:-2], b"x" * (1024 * 1024 + 1)],
                         ids=["empty", "invalid-header", "over-duration", "truncated", "over-size"])
def test_invalid_audio_never_egresses(audio):
    def handler(request):
        pytest.fail("Invalid segment egressed")

    async def run():
        with pytest.raises(TranscriptionError):
            await build(handler).transcribe(audio)
    asyncio.run(run())


@pytest.mark.parametrize("status,headers,body", [
    (401, {}, b"private provider detail"),
    (302, {"location": "https://example.com"}, b""),
    (200, {"content-type": "text/plain"}, b"private"),
    (200, {"content-type": "application/json", "content-encoding": "gzip"}, b"x"),
    (200, {"content-type": "application/json"}, b"x" * (64 * 1024 + 1)),
    (200, {"content-type": "application/json"}, b'{"text":42}'),
    (200, {"content-type": "application/json"}, b'{"text":"' + b"x" * 8193 + b'"}'),
], ids=["authentication", "redirect", "mime", "compressed", "oversized", "invalid-text", "long-text"])
def test_invalid_response_is_redacted_and_closed(status, headers, body):
    stream = AudioStream([body])

    async def run():
        with pytest.raises(TranscriptionError) as failed:
            await build(lambda request: httpx.Response(status, headers=headers, stream=stream)).transcribe(wav())
        assert "private" not in str(failed.value)
    asyncio.run(run())
    assert stream.closed


@pytest.mark.parametrize("cancel", [False, True])
def test_deadline_and_cancellation_close_owned_response(cancel):
    stream = AudioStream([], stall=True)

    async def run():
        client = build(lambda request: httpx.Response(200, headers={"content-type": "application/json"}, stream=stream),
                       timeout_s=0.03)
        task = asyncio.create_task(client.transcribe(wav()))
        if cancel:
            await stream.entered.wait()
            task.cancel()
        with pytest.raises(asyncio.CancelledError if cancel else TranscriptionError):
            await task
    asyncio.run(run())
    assert stream.closed


def test_empty_transcription_is_silence():
    stream = AudioStream([b'{"text":" "}'])
    assert asyncio.run(build(lambda request: httpx.Response(200, headers={"content-type": "application/json"},
                                                          stream=stream)).transcribe(wav())) == ""
