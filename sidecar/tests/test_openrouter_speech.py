"""Actual REST adapter with synthetic HTTP transport; no provider or device."""

import asyncio
import json

import httpx
import pytest

from starlight_voice.adapters.openrouter_speech import OpenRouterSpeech, SpeechError


class AudioStream(httpx.AsyncByteStream):
    def __init__(self, chunks=(), *, stall=False):
        self.chunks, self.stall = chunks, stall
        self.closed = False
        self.entered = asyncio.Event()

    async def __aiter__(self):
        self.entered.set()
        for chunk in self.chunks:
            yield chunk
        if self.stall:
            await asyncio.Event().wait()

    async def aclose(self):
        self.closed = True


async def text(*chunks):
    for chunk in chunks:
        yield chunk


def build(handler, **kwargs):
    return OpenRouterSpeech(api_key="test-inference-key", voice_id="george",
                            model="elevenlabs/eleven-v4-turbo", transport=httpx.MockTransport(handler), **kwargs)


def test_repeated_speech_keeps_verified_trust_and_closes_separate_clients(monkeypatch):
    import ssl

    original = httpx.AsyncClient
    clients, contexts, requests, streams = [], [], [], []

    def client(*args, **kwargs):
        context = kwargs["verify"]
        assert isinstance(context, ssl.SSLContext)
        assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
        assert context.cert_store_stats()["x509_ca"] > 0
        assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False

        def handler(request):
            requests.append(request)
            stream = AudioStream([b"\x01\x00"])
            streams.append(stream)
            return httpx.Response(200, headers={"Content-Type": "audio/pcm"}, stream=stream)

        kwargs["transport"] = httpx.MockTransport(handler)
        instance = original(*args, **kwargs)
        clients.append(instance)
        contexts.append(context)
        return instance

    monkeypatch.setattr(httpx, "AsyncClient", client)

    async def run():
        for index, token in enumerate(("first-speech-key", "second-speech-key")):
            speech = OpenRouterSpeech(api_key=token, voice_id="george", model="elevenlabs/eleven-v4-turbo")
            assert len(clients) == index  # Construction starts no HTTP/certificate work.
            assert [chunk async for chunk in speech.stream(text("Hello."))] == [b"\x01\x00"]

    asyncio.run(run())
    assert clients[0] is not clients[1] and all(item.is_closed for item in clients)
    assert contexts[0] is contexts[1] and all(stream.closed for stream in streams)
    assert [request.headers["authorization"] for request in requests] == [
        "Bearer first-speech-key", "Bearer second-speech-key",
    ]


@pytest.mark.parametrize("model", sorted(OpenRouterSpeech.MODELS))
def test_request_and_fragmented_pcm_are_exact_and_response_is_closed(model):
    async def run():
        stream = AudioStream([b"\x01", b"\x00\x02", b"\x00"])
        requests = []

        def handler(request):
            requests.append(request)
            return httpx.Response(200, headers={"Content-Type": "audio/pcm"}, stream=stream)

        client = OpenRouterSpeech(api_key="test-inference-key", voice_id="george", model=model,
                                  transport=httpx.MockTransport(handler))
        audio = [chunk async for chunk in client.stream(text("Hello", "."))]
        assert audio == [b"\x01\x00", b"\x02\x00"] and stream.closed
        assert len(requests) == 1
        request = requests[0]
        assert str(request.url) == "https://openrouter.ai/api/v1/audio/speech"
        assert request.headers["authorization"] == "Bearer test-inference-key"
        assert request.headers["accept-encoding"] == "identity"
        assert json.loads(request.content) == {
            "model": model, "input": "Hello.", "voice": "george", "response_format": "pcm",
        }
    asyncio.run(run())


@pytest.mark.parametrize("chunks", [(" ",), ("a" * 4096, "x"), (object(),), ("",) * 4097])
def test_invalid_input_is_rejected_before_http_egress(chunks):
    def handler(request):
        pytest.fail("Invalid speech input reached the provider")

    async def run():
        with pytest.raises(SpeechError):
            _ = [chunk async for chunk in build(handler).stream(text(*chunks))]
    asyncio.run(run())


@pytest.mark.parametrize("options", [
    {"api_key": "*not found*"}, {"api_key": ""}, {"voice_id": ""},
    {"model": "eleven_v4_turbo"}, {"timeout_s": float("nan")}, {"timeout_s": 121},
])
def test_invalid_selection_is_rejected_during_construction(options):
    defaults = {"api_key": "test", "voice_id": "chosen", "model": "elevenlabs/eleven-v4-turbo"}
    with pytest.raises(ValueError):
        OpenRouterSpeech(**(defaults | options))


@pytest.mark.parametrize("status,headers", [
    (401, {}), (429, {}), (500, {}), (302, {"location": "https://other.example/steal"}),
    (200, {"content-type": "application/json"}),
    (200, {"content-type": "audio/pcm", "content-encoding": "gzip"}),
])
def test_rejection_redirect_and_non_pcm_are_redacted_without_followup(status, headers):
    async def run():
        stream = AudioStream([b"private-key-and-prompt"])
        requests = []

        def handler(request):
            requests.append(request)
            return httpx.Response(status, headers=headers, stream=stream)

        with pytest.raises(SpeechError) as exc:
            _ = [chunk async for chunk in build(handler).stream(text("private prompt"))]
        assert "private" not in str(exc.value)
        assert not stream.entered.is_set() and stream.closed and len(requests) == 1
    asyncio.run(run())


@pytest.mark.parametrize("chunks", [[], [b"\x01"], [b"\x00" * (OpenRouterSpeech.MAX_AUDIO_BYTES + 2)]])
def test_empty_truncated_and_oversized_audio_fail_closed(chunks):
    async def run():
        stream = AudioStream(chunks)
        client = build(lambda request: httpx.Response(200, headers={"content-type": "audio/pcm"}, stream=stream))
        with pytest.raises(SpeechError):
            _ = [chunk async for chunk in client.stream(text("Hi."))]
        assert stream.closed
    asyncio.run(run())


def test_network_exception_does_not_leak_the_provider_or_prompt():
    def handler(request):
        raise RuntimeError("test-inference-key private prompt")

    async def run():
        with pytest.raises(SpeechError) as exc:
            _ = [chunk async for chunk in build(handler).stream(text("private prompt"))]
        assert str(exc.value) == "OpenRouter speech failed or timed out"
        assert exc.value.__cause__ is None and exc.value.__suppress_context__
    asyncio.run(run())


def test_consumer_closure_releases_response_and_keeps_first_chunk_unbuffered():
    async def run():
        stream = AudioStream([b"\x01\x00"], stall=True)
        client = build(lambda request: httpx.Response(200, headers={"content-type": "audio/pcm"}, stream=stream))
        audio = client.stream(text("Hi."))
        assert await anext(audio) == b"\x01\x00"
        await audio.aclose()
        assert stream.closed
        assert asyncio.all_tasks() == {asyncio.current_task()}
    asyncio.run(run())


@pytest.mark.parametrize("cancel", [False, True])
def test_timeout_or_caller_cancellation_closes_the_pending_response(cancel):
    async def run():
        stream = AudioStream(stall=True)
        client = build(lambda request: httpx.Response(200, headers={"content-type": "audio/pcm"}, stream=stream),
                       timeout_s=1 if cancel else 0.02)

        async def collect():
            return [chunk async for chunk in client.stream(text("Hi."))]

        task = asyncio.create_task(collect())
        await stream.entered.wait()
        if cancel:
            task.cancel()
        with pytest.raises(asyncio.CancelledError if cancel else SpeechError):
            await task
        assert stream.closed
        assert asyncio.all_tasks() == {asyncio.current_task()}
    asyncio.run(run())


def test_total_deadline_also_bounds_waiting_for_text_before_http():
    async def waiting_text():
        await asyncio.Event().wait()
        yield "unreachable"

    async def run():
        client = build(lambda request: pytest.fail("No text was ready"), timeout_s=0.02)
        with pytest.raises(SpeechError):
            _ = [chunk async for chunk in client.stream(waiting_text())]
    asyncio.run(run())
