"""Actual SmallWebRTC SDP/data-channel/cleanup, with deterministic graph services."""

import asyncio
import json

import pytest

pytest.importorskip("pipecat")
pytest.importorskip("aiortc")

from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription  # noqa: E402
from pipecat.frames.frames import LLMFullResponseStartFrame, LLMTextFrame  # noqa: E402

from starlight_voice import voice_loop  # noqa: E402
from starlight_voice.component_session import ComponentSessions  # noqa: E402
from starlight_voice.config import Settings  # noqa: E402
from starlight_voice.native_bridge import NativeBridge  # noqa: E402
from test_component_turns_pipecat import MarkerVAD, PassThrough, RecordingModel, RecordingSTT  # noqa: E402


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
            await manager.stop({"session_id": scope["session_id"]}, scope["capability"])
            assert bridge.session is None and manager.current is None
            assert runtime.peer is None and runtime.worker is None and runtime.task is None
        finally:
            await manager.close()
            await client.close()

    asyncio.run(asyncio.wait_for(run(), 20))
