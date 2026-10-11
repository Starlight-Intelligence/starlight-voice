"""Bounded fixture loop tests: no server, network, SDK, microphone or model."""

import asyncio
import threading
import unittest

from starlight_voice.component_host import ComponentHost
from starlight_voice.component_session import ComponentSessions
from starlight_voice.config import Settings
from starlight_voice.native_bridge import BridgeError, NativeBridge

SDP = "v=0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\n"
SPEC = {"configured": True, "voices": ["fixture-voice"]}


class Resource:
    def __init__(self, *args):
        self.loop = asyncio.get_running_loop()
        self.closed = False
        self.survived_request = False
        self.tick = asyncio.create_task(self._tick())

    async def _tick(self):
        await asyncio.sleep(0.02)
        self.survived_request = True

    async def initialize(self, sdp):
        return {"type": "answer", "sdp": SDP}

    async def close(self):
        self.closed = True
        self.tick.cancel()
        await asyncio.gather(self.tick, return_exceptions=True)


class ComponentHostTests(unittest.TestCase):
    def setUp(self):
        self.resources = []

        def factory(*args):
            resource = Resource(*args)
            self.resources.append(resource)
            return resource

        self.host = ComponentHost(
            NativeBridge(), descriptor=lambda: SPEC,
            manager_factory=lambda bridge: ComponentSessions(
                bridge, factory=factory,
                settings=lambda: Settings(tts_voice_id="fixture-voice"),
                spec=lambda settings: SPEC,
            ),
        )

    def tearDown(self):
        self.host.close()
        if self.host.thread:
            self.assertFalse(self.host.thread.is_alive())

    def test_capability_discovery_and_unauthorized_offer_create_no_loop(self):
        self.assertEqual(self.host.capabilities(), SPEC)
        self.assertIsNone(self.host.thread)
        with self.assertRaises(BridgeError):
            self.host.call("offer", {"session_id": "unknown", "sdp": SDP, "type": "offer"}, "unknown")
        self.assertIsNone(self.host.thread)

    def test_pipeline_lifetime_survives_requests_and_shutdown_joins_the_owned_loop(self):
        session = self.host.call("start", {"provider": "elevenlabs", "voice": "fixture-voice"})
        answer = self.host.call("offer", {"session_id": session["session_id"], "type": "offer", "sdp": SDP},
                                session["capability"])
        self.assertEqual(answer["type"], "answer")
        self.assertIs(self.resources[0].loop, self.host.loop)
        # Await an actual task handle on the same loop, not an arbitrary observation sleep.
        async def finished_tick():
            await self.resources[0].tick
        asyncio.run_coroutine_threadsafe(finished_tick(), self.host.loop).result(timeout=2)
        self.assertTrue(self.resources[0].survived_request)
        result = self.host.try_stop({"session_id": session["session_id"]}, session["capability"])
        self.assertTrue(result["runtime_closed"])
        self.assertTrue(self.resources[0].closed)
        self.host.close()
        self.assertFalse(self.host.thread.is_alive())
        self.host.close()
        self.assertTrue(self.host.closed)

    def test_wrong_stop_identity_does_not_close_another_session(self):
        session = self.host.call("start", {"provider": "elevenlabs", "voice": "fixture-voice"})
        self.assertIsNone(self.host.try_stop({"session_id": "another"}, session["capability"]))
        self.assertIsNotNone(self.host.manager.current)
        with self.assertRaises(BridgeError):
            self.host.try_stop({"session_id": session["session_id"]}, "wrong")
        self.assertIsNotNone(self.host.manager.current)

    def test_unconfigured_start_never_creates_a_thread(self):
        self.host.descriptor = lambda: {"configured": False, "voices": []}
        with self.assertRaises(BridgeError):
            self.host.call("start", {"provider": "elevenlabs", "voice": "fixture-voice"})
        self.assertIsNone(self.host.thread)
        self.assertEqual([t for t in threading.enumerate() if t.name == "voice-component-owned-loop"], [])

    def test_failed_host_shutdown_keeps_exact_loop_and_scope_until_retry(self):
        session = self.host.call("start", {"provider": "elevenlabs", "voice": "fixture-voice"})
        self.host.call("offer", {"session_id": session["session_id"], "type": "offer", "sdp": SDP},
                       session["capability"])
        resource = self.resources[0]
        original = resource.close

        async def fails():
            raise RuntimeError("private failure")

        resource.close = fails
        loop, thread, manager = self.host.loop, self.host.thread, self.host.manager
        with self.assertRaisesRegex(BridgeError, "cleanup incomplete"):
            self.host.close()
        self.assertIs(self.host.loop, loop)
        self.assertIs(self.host.manager, manager)
        self.assertTrue(thread.is_alive())
        self.assertTrue(self.host.bridge.session.runtime_owned)
        resource.close = original
        self.host.close()
        self.assertFalse(thread.is_alive())
        self.assertIsNone(self.host.bridge.session)


if __name__ == "__main__":
    unittest.main()
