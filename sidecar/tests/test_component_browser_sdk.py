"""Actual SmallWebRTC SDP/data-channel/cleanup, with deterministic graph services."""

import asyncio
import json
import math
import struct

import pytest

pytest.importorskip("pipecat")
pytest.importorskip("aiortc")

from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription  # noqa: E402
from pipecat.frames.frames import (  # noqa: E402
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TTSAudioRawFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
)
from test_component_turns_pipecat import MarkerVAD, PassThrough, RecordingModel, RecordingSTT  # noqa: E402

from starlight_voice import voice_loop  # noqa: E402
from starlight_voice.component_session import ComponentSessions  # noqa: E402
from starlight_voice.config import Settings  # noqa: E402
from starlight_voice.native_bridge import NativeBridge  # noqa: E402


def test_actual_peer_negotiation_caption_delivery_and_owned_shutdown(monkeypatch):
    async def run():
        monkeypatch.setattr(voice_loop.adapters, "require", lambda engine: None)
        monkeypatch.setattr(voice_loop, "_stt_service", lambda settings: RecordingSTT())
        monkeypatch.setattr(voice_loop, "_llm_service", lambda settings: RecordingModel())
        monkeypatch.setattr(voice_loop, "_tts_service", lambda settings: PassThrough())
        monkeypatch.setattr(voice_loop, "_live_vad_analyzer", MarkerVAD)
        # This factory must not import LocalAudioTransport or a microphone.
        bridge = NativeBridge()
        manager = ComponentSessions(
            bridge, settings=lambda: Settings(tts_voice_id="fixture-voice"),
            spec=lambda settings: {"configured": True, "voices": ["fixture-voice"]},
        )
        scope = await manager.start({"provider": "elevenlabs", "voice": "fixture-voice"})
        client = RTCPeerConnection(RTCConfiguration(iceServers=[]))
        client.addTransceiver("audio", direction="sendrecv")
        channel = client.createDataChannel("pipecat")
        caption = asyncio.Event()
        opened_channel = asyncio.Event()
        received = []
        audio_tracks = []

        @client.on("track")
        def track_received(track):
            if track.kind == "audio":
                audio_tracks.append(track)

        @channel.on("open")
        def opened():
            channel.send("ping")
            opened_channel.set()

        @channel.on("message")
        def message(data):
            if isinstance(data, str):
                value = json.loads(data)
                received.append(value)
                if value.get("text") == "Synthetic reply":
                    caption.set()

        try:
            offer = await client.createOffer()
            await client.setLocalDescription(offer)
            answer = await manager.offer({"session_id": scope["session_id"], "type": "offer",
                                          "sdp": client.localDescription.sdp}, scope["capability"])
            runtime = manager.current.runtime
            assert runtime.task is not None and not runtime.task.done()
            assert bridge.session.runtime_owned and bridge.session.remaining_calls == 20
            await client.setRemoteDescription(RTCSessionDescription(type=answer["type"], sdp=answer["sdp"]))
            await asyncio.wait_for(opened_channel.wait(), 5)
            # Actual server-to-client frames over the negotiated data channel; no inference/audio.
            await runtime.worker.queue_frames([LLMFullResponseStartFrame(), LLMTextFrame("Synthetic reply")])
            await asyncio.wait_for(caption.wait(), 5)
            assert any(item.get("reset") is True for item in received)
            assert received[-1] == {"type": "component.caption", "text": "Synthetic reply"}
            # Real audio packets/codecs, generated marker PCM only. No provider or device.
            assert len(audio_tracks) == 1
            tone = b"".join(struct.pack("<h", int(6000 * math.sin(2 * math.pi * 440 * sample / 24000)))
                            for sample in range(4800))
            await runtime.worker.queue_frames([TTSStartedFrame(), TTSAudioRawFrame(tone, 24000, 1), TTSStoppedFrame()])

            async def receive_marker():
                for _ in range(100):
                    frame = await audio_tracks[0].recv()
                    samples = frame.to_ndarray().astype("int32")
                    if abs(samples).max() > 1000:
                        return
                raise AssertionError("Synthetic PCM did not reach the negotiated audio track")

            await asyncio.wait_for(receive_marker(), 5)
            await manager.stop({"session_id": scope["session_id"]}, scope["capability"])
            assert bridge.session is None and manager.current is None
            assert runtime.peer is None and runtime.worker is None and runtime.task is None
        finally:
            await manager.close()
            await client.close()

    asyncio.run(asyncio.wait_for(run(), 20))
