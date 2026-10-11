"""Lazy binding to the SDK's existing segmented WAV and turn lifecycle."""
from __future__ import annotations

from .openrouter_transcription import OpenRouterTranscription, TranscriptionError


def create_openrouter_transcription_stt(*, api_key: str, model: str, transcription=None):
    client = transcription or OpenRouterTranscription(api_key=api_key, model=model)
    from pipecat.frames.frames import ErrorFrame, TranscriptionFrame
    from pipecat.services.settings import STTSettings
    from pipecat.services.stt_service import SegmentedSTTService
    from pipecat.utils.time import time_now_iso8601

    class OpenRouterTranscriptionService(SegmentedSTTService):
        def __init__(self):
            super().__init__(settings=STTSettings(model=model), sample_rate=16000)

        async def run_stt(self, audio):
            await self.start_processing_metrics()
            try:
                text = await client.transcribe(audio)
                if text:
                    yield TranscriptionFrame(text, self._user_id, time_now_iso8601())
            except TranscriptionError:
                yield ErrorFrame(error="OpenRouter transcription failed or timed out", fatal=True)
            finally:
                await self.stop_processing_metrics()

    return OpenRouterTranscriptionService()
