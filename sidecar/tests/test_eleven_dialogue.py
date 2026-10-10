import asyncio
import base64
import json

import pytest

from starlight_voice.adapters.eleven_dialogue import DialogueError, ElevenDialogue


class Socket:
    def __init__(self, messages):
        self.messages = iter(messages)
        self.sent = []
        self.closed = False
        self.flushed = asyncio.Event()

    async def send(self, raw):
        message = json.loads(raw)
        self.sent.append(message)
        if message.get("close_socket"):
            self.flushed.set()

    async def recv(self):
        await self.flushed.wait()
        return json.dumps(next(self.messages))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        self.closed = True


async def chunks():
    yield "Hi."


def test_v4_uses_dialogue_endpoint_registration_flush_and_pcm():
    async def run():
        socket = Socket([{"audio": base64.b64encode(b"\x01\x00").decode(), "is_final": True}])
        captured = {}

        def connect(url, **kwargs):
            captured.update(url=url, **kwargs)
            return socket

        client = ElevenDialogue(api_key="secret-test", voice_id="chosen", connect=connect)
        output = [chunk async for chunk in client.stream(chunks())]
        assert output == [b"\x01\x00"]
        assert "/text-to-dialogue/stream-input?" in captured["url"]
        assert "model_id=eleven_v4_turbo" in captured["url"]
        assert "output_format=pcm_48000" in captured["url"]
        assert "secret-test" not in captured["url"]
        assert captured["proxy"] is None
        assert socket.sent == [{"voices": ["chosen"]},
                               {"inputs": [{"text": "Hi.", "voice_id": "chosen", "new_turn": False}]},
                               {"close_socket": True}]
        assert socket.closed
    asyncio.run(run())


@pytest.mark.parametrize("message", [{"error": "secret-provider-detail"},
                                      {"audio": "invalid-base64"}, {"audio": "AQ=="}, []])
def test_provider_and_audio_errors_close_the_connection_without_leaking_details(message):
    async def run():
        socket = Socket([message])
        client = ElevenDialogue(api_key="secret-test", voice_id="chosen", connect=lambda *a, **k: socket)
        with pytest.raises(DialogueError) as exc:
            _ = [chunk async for chunk in client.stream(chunks())]
        assert "secret" not in str(exc.value)
        assert socket.closed
    asyncio.run(run())


def test_stalled_provider_has_a_total_deadline_and_no_owned_tasks_left():
    async def run():
        class Stalled(Socket):
            async def recv(self):
                await asyncio.Event().wait()
        socket = Stalled([])
        client = ElevenDialogue(api_key="key", voice_id="chosen", timeout_s=0.02,
                                connect=lambda *a, **k: socket)
        with pytest.raises(DialogueError):
            _ = [chunk async for chunk in client.stream(chunks())]
        assert socket.closed
        assert asyncio.all_tasks() == {asyncio.current_task()}
    asyncio.run(run())


def test_consumer_cancellation_closes_socket_and_sender():
    async def run():
        socket = Socket([{"audio": "AQA="}, {"is_final": True}])
        client = ElevenDialogue(api_key="key", voice_id="chosen", connect=lambda *a, **k: socket)
        stream = client.stream(chunks())
        assert await anext(stream) == b"\x01\x00"
        await stream.aclose()
        assert socket.closed
        assert asyncio.all_tasks() == {asyncio.current_task()}
    asyncio.run(run())


def test_sender_error_is_propagated_while_receiver_is_waiting():
    async def bad_chunks():
        raise RuntimeError("secret-input")
        yield "unreachable"

    async def run():
        socket = Socket([])
        client = ElevenDialogue(api_key="key", voice_id="chosen", connect=lambda *a, **k: socket)
        with pytest.raises(DialogueError):
            _ = [chunk async for chunk in client.stream(bad_chunks())]
        assert socket.closed
        assert asyncio.all_tasks() == {asyncio.current_task()}
    asyncio.run(run())

