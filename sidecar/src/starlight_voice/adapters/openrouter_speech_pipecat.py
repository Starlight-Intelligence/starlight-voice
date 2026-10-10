"""Lazy sentence-level Pipecat binding for the opt-in REST speech alternative."""

from __future__ import annotations

import asyncio
from contextlib import aclosing

from .openrouter_speech import OpenRouterSpeech, SpeechError


def create_openrouter_speech_tts(*, api_key: str, voice_id: str, model: str,
                                timeout_s: float = 30, speech=None):
    client = speech or OpenRouterSpeech(api_key=api_key, voice_id=voice_id, model=model, timeout_s=timeout_s)
    from pipecat.frames.frames import ErrorFrame, TTSAudioRawFrame, TTSStoppedFrame
    from pipecat.services.settings import TTSSettings
    from pipecat.services.tts_service import TextAggregationMode, TTSService

    class OpenRouterSpeechTTSService(TTSService):
        def __init__(self):
            super().__init__(
                settings=TTSSettings(model=model, voice=voice_id), sample_rate=OpenRouterSpeech.SAMPLE_RATE,
                text_aggregation_mode=TextAggregationMode.SENTENCE,
                push_start_frame=True, push_stop_frames=True, stop_frame_timeout_s=timeout_s + 3,
            )
            self._synthesis_task = None
            self._synthesis_context = None

        def can_generate_metrics(self):
            return True

        async def flush_audio(self, context_id: str | None = None):
            if context_id and self.audio_context_available(context_id):
                await self.append_to_audio_context(context_id, TTSStoppedFrame(context_id=context_id))
                await self.remove_audio_context(context_id)

        async def run_tts(self, text: str, context_id: str):
            async def sentence():
                yield text

            self._synthesis_task = asyncio.current_task()
            self._synthesis_context = context_id
            try:
                await self.start_tts_usage_metrics(text)
                async with aclosing(client.stream(sentence())) as audio:
                    first_audio = True
                    async for chunk in audio:
                        if not chunk:
                            continue
                        if first_audio:
                            await self.stop_ttfb_metrics()
                            first_audio = False
                        yield TTSAudioRawFrame(chunk, OpenRouterSpeech.SAMPLE_RATE, 1, context_id=context_id)
                    if first_audio:
                        yield ErrorFrame(error="OpenRouter speech returned no audio", fatal=True)
            except SpeechError:
                yield ErrorFrame(error="OpenRouter speech synthesis failed or timed out", fatal=True)
            finally:
                if self._synthesis_task is asyncio.current_task() and self._synthesis_context == context_id:
                    self._synthesis_task = None
                    self._synthesis_context = None
                await self.stop_ttfb_metrics()

        async def on_audio_context_interrupted(self, context_id: str):
            task = self._synthesis_task
            if (self._synthesis_context == context_id and task is not None
                    and task is not asyncio.current_task() and not task.done()):
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await super().on_audio_context_interrupted(context_id)

    return OpenRouterSpeechTTSService()
