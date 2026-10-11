"""Explicit OpenRouter REST speech, returning bounded 24 kHz mono PCM16.

No connection opens on import/construction. HTTP chunk arrival is not proof of
upstream synthesis latency. Closing the stream does not prove billing stopped.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import AsyncIterable, AsyncIterator

from ..http_transport import verified_context


class SpeechError(RuntimeError):
    """Redacted synthesis failure; no provider body, credential or prompt escapes."""


class OpenRouterSpeech:
    SAMPLE_RATE = 24000
    MAX_CHARACTERS = 4096
    MAX_AUDIO_BYTES = 2 * 1024 * 1024
    MODELS = frozenset({"elevenlabs/eleven-v4-turbo", "elevenlabs/eleven-v3-conversational"})

    def __init__(self, *, api_key: str, voice_id: str, model: str, timeout_s: float = 30, transport=None):
        if not isinstance(api_key, str) or not api_key.strip() or api_key.strip() == "*not found*":
            raise ValueError("An OpenRouter inference API key is required")
        if not isinstance(voice_id, str) or not voice_id.strip() or len(voice_id) > 128:
            raise ValueError("An explicit OpenRouter voice name or ID is required")
        if model not in self.MODELS:
            raise ValueError("Select a supported OpenRouter ElevenLabs dialogue model")
        if not math.isfinite(timeout_s) or not 0 < timeout_s <= 120:
            raise ValueError("Synthesis timeout must be between 0 and 120 seconds")
        self._api_key = api_key
        self.voice_id = voice_id
        self.model = model
        self.timeout_s = timeout_s
        self._transport = transport

    async def stream(self, text_chunks: AsyncIterable[str]) -> AsyncIterator[bytes]:
        try:
            # REST takes one complete sentence. Bound aggregation before any egress.
            async with asyncio.timeout(self.timeout_s):
                parts, count, chunk_count = [], 0, 0
                async for text in text_chunks:
                    chunk_count += 1
                    if chunk_count > self.MAX_CHARACTERS:
                        raise SpeechError("Speech text stream exceeds the turn allowance")
                    if not isinstance(text, str):
                        raise SpeechError("Speech input must contain text")
                    count += len(text)
                    if count > self.MAX_CHARACTERS:
                        raise SpeechError("Speech input exceeds the turn allowance")
                    if text:
                        parts.append(text)
                text = "".join(parts)
                if not text.strip():
                    raise SpeechError("Speech input is empty")

                import httpx

                async with httpx.AsyncClient(
                    trust_env=False, follow_redirects=False, timeout=min(self.timeout_s, 5),
                    transport=self._transport,
                    verify=verified_context() if self._transport is None else True,
                ) as client:
                    async with client.stream(
                        "POST", "https://openrouter.ai/api/v1/audio/speech",
                        headers={"Authorization": f"Bearer {self._api_key}", "Accept-Encoding": "identity"},
                        json={"model": self.model, "input": text, "voice": self.voice_id, "response_format": "pcm"},
                    ) as response:
                        if response.status_code != 200:
                            raise SpeechError("OpenRouter rejected speech synthesis")
                        if response.headers.get("content-type", "").split(";")[0].strip().lower() != "audio/pcm":
                            raise SpeechError("Speech response is not PCM audio")
                        if response.headers.get("content-encoding", "identity").lower() != "identity":
                            raise SpeechError("Compressed speech response is unsupported")
                        received, remainder = 0, b""
                        # Raw iteration avoids accumulating a fixed-size first chunk or
                        # decompressing an unbounded response into the audio buffer.
                        async for chunk in response.aiter_raw():
                            received += len(chunk)
                            if received > self.MAX_AUDIO_BYTES:
                                raise SpeechError("Speech audio exceeds the turn allowance")
                            chunk = remainder + chunk
                            aligned = len(chunk) - len(chunk) % 2
                            remainder = chunk[aligned:]
                            for offset in range(0, aligned, 8192):
                                yield chunk[offset:min(offset + 8192, aligned)]
                        if remainder:
                            raise SpeechError("Speech ended with an incomplete PCM16 sample")
                        if not received:
                            raise SpeechError("Speech returned no audio")
        except (SpeechError, GeneratorExit):
            raise
        except Exception:
            raise SpeechError("OpenRouter speech failed or timed out") from None
