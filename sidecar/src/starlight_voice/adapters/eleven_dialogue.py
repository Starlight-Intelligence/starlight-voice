"""ElevenLabs v4 Turbo's dialogue protocol, separate from the Flash TTS socket.

No device, server or connection opens on import. stream() is an explicit provider call.
The returned bytes are 48 kHz PCM16, ready for a future Pipecat/device adapter.
"""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import AsyncIterable, AsyncIterator
from urllib.parse import urlencode


class DialogueError(RuntimeError):
    """A synthesis turn failed; provider messages are deliberately not exposed."""


class ElevenDialogue:
    def __init__(self, *, api_key: str, voice_id: str, model: str = "eleven_v4_turbo",
                 timeout_s: float = 30, connect=None):
        if not api_key.strip() or not voice_id.strip():
            raise ValueError("An ElevenLabs API key and a selected voice ID are required")
        if model not in {"eleven_v4_turbo", "eleven_v3_conversational"}:
            raise ValueError("Select a realtime dialogue model, not a Flash TTS model")
        if not 0 < timeout_s <= 120:
            raise ValueError("Synthesis timeout must be between 0 and 120 seconds")
        self._api_key = api_key
        self.voice_id = voice_id
        self.model = model
        self.timeout_s = timeout_s
        self._connect = connect

    async def stream(self, text_chunks: AsyncIterable[str]) -> AsyncIterator[bytes]:
        connect = self._connect
        if connect is None:
            from websockets.asyncio.client import connect
        url = "wss://api.elevenlabs.io/v1/text-to-dialogue/stream-input?" + urlencode({
            "model_id": self.model, "output_format": "pcm_48000",
        })
        try:
            async with asyncio.timeout(self.timeout_s):
                async with connect(url, additional_headers={"xi-api-key": self._api_key},
                                   open_timeout=5, close_timeout=2, max_size=2**20) as socket:
                    await socket.send(json.dumps({"voices": [self.voice_id]}))

                    async def send_text():
                        async for text in text_chunks:
                            if not isinstance(text, str):
                                raise TypeError("Text chunks must be strings")
                            if text:
                                await socket.send(json.dumps({"inputs": [{
                                    "text": text, "voice_id": self.voice_id, "new_turn": False,
                                }]}))
                        # This flushes even short replies below the provider's buffer threshold.
                        await socket.send(json.dumps({"close_socket": True}))

                    sender = asyncio.create_task(send_text())
                    receiver = None
                    try:
                        while True:
                            receiver = asyncio.create_task(socket.recv())
                            watched = {receiver} if sender.done() else {receiver, sender}
                            await asyncio.wait(watched, return_when=asyncio.FIRST_COMPLETED)
                            if sender.done():
                                sender.result()
                            raw = await receiver
                            message = json.loads(raw)
                            if not isinstance(message, dict) or message.get("error"):
                                raise DialogueError("ElevenLabs rejected the synthesis turn")
                            if message.get("audio"):
                                audio = base64.b64decode(message["audio"], validate=True)
                                if len(audio) % 2:
                                    raise DialogueError("Received incomplete PCM16 samples")
                                yield audio
                            if message.get("is_final"):
                                if not sender.done():
                                    raise DialogueError("Provider ended synthesis before text submission completed")
                                return
                    finally:
                        pending = [task for task in (sender, receiver) if task is not None]
                        for task in pending:
                            if not task.done():
                                task.cancel()
                        await asyncio.gather(*pending, return_exceptions=True)
        except (DialogueError, GeneratorExit):
            raise
        except Exception:
            raise DialogueError("ElevenLabs dialogue synthesis failed or timed out") from None
