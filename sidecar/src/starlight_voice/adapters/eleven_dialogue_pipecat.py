"""Lazy Pipecat binding for ElevenLabs' dialogue socket.

The base service owns sentence aggregation, audio ordering and interruption. Each
aggregated sentence uses a fresh, flushed dialogue socket. This does not preserve
provider prosody across sentences or claim device playback verification.
"""

from __future__ import annotations

import asyncio
from contextlib import aclosing

from .eleven_dialogue import DialogueError, ElevenDialogue


def create_dialogue_tts(*, api_key: str, voice_id: str, model: str,
                        timeout_s: float = 30, dialogue=None):
    # Validate before importing the optional graph or opening any connection.
    client = dialogue or ElevenDialogue(api_key=api_key, voice_id=voice_id,
                                       model=model, timeout_s=timeout_s)
    from pipecat.frames.frames import ErrorFrame, TTSAudioRawFrame, TTSStoppedFrame
    from pipecat.services.settings import TTSSettings
    from pipecat.services.tts_service import TextAggregationMode, TTSService

    class ElevenDialogueTTSService(TTSService):
        def __init__(self):
            super().__init__(
                settings=TTSSettings(model=model, voice=voice_id, language=None), sample_rate=48000,
                text_aggregation_mode=TextAggregationMode.SENTENCE,
                push_start_frame=True, push_stop_frames=True,
                # The base queue must remain alive during the synthesis deadline.
                stop_frame_timeout_s=timeout_s + 3,
            )
            self._synthesis_task = None
            self._synthesis_context = None

        def can_generate_metrics(self):
            return True

        async def flush_audio(self, context_id: str | None = None):
            # Synthesis is synchronous with run_tts: no receive loop remains at
            # turn completion. Close even when no audio was yielded; older base
            # services otherwise leave failed contexts until their idle timeout.
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
                        yield TTSAudioRawFrame(chunk, 48000, 1, context_id=context_id)
                    if first_audio:
                        yield ErrorFrame(error="ElevenLabs dialogue returned no audio", fatal=True)
            except DialogueError:
                # End the worker rather than leaving a silent audio context alive
                # or silently switching the selected provider/voice.
                yield ErrorFrame(error="ElevenLabs dialogue synthesis failed or timed out", fatal=True)
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

    return ElevenDialogueTTSService()
