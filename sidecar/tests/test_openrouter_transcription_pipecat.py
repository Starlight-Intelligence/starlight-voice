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
