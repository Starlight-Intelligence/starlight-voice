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
