"""Real broker/session lifecycle with deterministic resources; no media or providers."""

import asyncio
import gc
import os
import unittest
from unittest.mock import patch

from starlight_voice.component_session import ComponentSessions, component_spec, validate_offer
from starlight_voice.component_tools import ComponentReadTools
from starlight_voice.config import Settings
from starlight_voice.native_bridge import BridgeError, NativeBridge

SDP = "v=0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\nm=application 9 UDP/DTLS/SCTP webrtc-datachannel\r\n"


class Resource:
    def __init__(self, settings, bridge, scope, ended):
        self.started = asyncio.Event()
        self.continue_start = asyncio.Event()
        self.continue_start.set()
        self.close_failures = 0
        self.close_calls = 0
        self.ended = ended

    async def initialize(self, sdp):
        self.started.set()
        await self.continue_start.wait()
        return {"type": "answer", "sdp": SDP, "pc_id": "fixture-peer"}

    async def close(self):
        self.close_calls += 1
        if self.close_failures:
            self.close_failures -= 1
            raise RuntimeError("Private provider error must not reach the caller")


class ComponentSessionsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.clock = [0]
        self.mints = []

        async def mint(provider, voice):
            self.mints.append(provider)
            return {"value": "fixture", "expires_at": 9999999999}

        self.bridge = NativeBridge(clock=lambda: self.clock[0], mint=mint)
        self.resources = []
        self.delay_start = False

        def factory(*args):
            resource = Resource(*args)
            if self.delay_start:
                resource.continue_start.clear()
            self.resources.append(resource)
            return resource

        self.manager = ComponentSessions(
            self.bridge, factory=factory,
            settings=lambda: Settings(tts_voice_id="fixture-voice", tts_model="eleven_v4_turbo"),
            spec=lambda settings: {"configured": True, "voices": ["fixture-voice"]},
        )
        self.scope = await self.manager.start({"provider": "elevenlabs", "voice": "fixture-voice"})

    async def asyncTearDown(self):
        for resource in self.resources:
            resource.close_failures = 0
        await self.manager.close()

    def offer(self, sdp=SDP):
        return {"session_id": self.scope["session_id"], "sdp": sdp, "type": "offer"}

    def identity(self):
        return {"session_id": self.scope["session_id"]}

    async def test_start_reserves_existing_broker_without_creating_media_or_minting_keys(self):
        self.assertEqual(self.resources, [])
        self.assertEqual(self.mints, [])
        self.assertNotIn("client_secret", self.scope)
        self.assertTrue(self.bridge.session.runtime_owned)
        self.assertFalse(self.scope["runtime_verified"])

    async def test_native_start_is_refused_even_after_read_authority_expires(self):
        self.clock[0] = 301
        with self.assertRaises(BridgeError) as held:
            await self.bridge.start({"provider": "grok", "voice": "ara"})
        self.assertEqual(held.exception.status, 429)
        self.assertEqual(self.mints, [])

    async def test_plain_broker_stop_cannot_release_physical_runtime_reservation(self):
        with self.assertRaises(BridgeError) as held:
            self.bridge.stop(self.identity(), self.scope["capability"])
        self.assertEqual(held.exception.status, 409)
        self.assertIsNotNone(self.bridge.session)

    async def test_duplicate_offer_reuses_one_runtime_and_changed_offer_is_refused(self):
        first = await self.manager.offer(self.offer(), self.scope["capability"])
        second = await self.manager.offer(self.offer(), self.scope["capability"])
        self.assertEqual(first, second)
        self.assertEqual(len(self.resources), 1)
        with self.assertRaises(BridgeError) as changed:
            await self.manager.offer(self.offer(SDP + "a=sendrecv\r\n"), self.scope["capability"])
        self.assertEqual(changed.exception.status, 409)
        self.assertEqual(len(self.resources), 1)

    async def test_wrong_capability_and_video_offer_never_create_a_runtime(self):
        with self.assertRaises(BridgeError) as unauthorized:
            await self.manager.offer(self.offer(), "wrong")
        self.assertEqual(unauthorized.exception.status, 401)
        with self.assertRaises(BridgeError):
            await self.manager.offer(self.offer(SDP + "m=video 9 UDP/TLS/RTP/SAVPF 96\r\n"),
                                     self.scope["capability"])
        self.assertEqual(self.resources, [])

    async def test_failed_cleanup_revokes_reads_holds_reservation_and_allows_stop_retry(self):
        await self.manager.offer(self.offer(), self.scope["capability"])
        self.resources[0].close_failures = 1
        with self.assertRaises(BridgeError) as unavailable:
            await self.manager.stop(self.identity(), self.scope["capability"])
        self.assertEqual(unavailable.exception.status, 503)
        self.assertNotIn("Private provider", str(unavailable.exception))
        with self.assertRaises(BridgeError):
            self.bridge.authorize(self.identity(), self.scope["capability"])
        self.clock[0] = 301
        with self.assertRaises(BridgeError):
            await self.bridge.start({"provider": "grok", "voice": "ara"})
        result = await self.manager.stop(self.identity(), self.scope["capability"])
        self.assertTrue(result["runtime_closed"])
        self.assertEqual(self.resources[0].close_calls, 2)
        self.assertIsNone(self.bridge.session)
        duplicate = await self.manager.stop(self.identity(), self.scope["capability"])
        self.assertTrue(duplicate["duplicate"])

    async def test_cancelled_offer_waiter_retains_owned_start_until_stop(self):
        self.delay_start = True
        waiter = asyncio.create_task(self.manager.offer(self.offer(), self.scope["capability"]))
        while not self.resources:
            await asyncio.sleep(0)
        await self.resources[0].started.wait()
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        self.assertFalse(self.manager.current.offer_task.done())
        await self.manager.stop(self.identity(), self.scope["capability"])
        self.assertEqual(self.resources[0].close_calls, 1)
        self.assertIsNone(self.bridge.session)

    async def test_slow_startup_cancellation_returns_retry_without_releasing_or_interrupting_cleanup(self):
        entered, cancelling, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        cancellations = []

        async def delayed_cancellation(sdp):
            entered.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                cancellations.append(True)
                cancelling.set()
                await release.wait()
                raise

        def factory(*args):
            resource = Resource(*args)
            resource.initialize = delayed_cancellation
            self.resources.append(resource)
            return resource

        self.manager.factory = factory
        waiter = asyncio.create_task(self.manager.offer(self.offer(), self.scope["capability"]))
        await entered.wait()
        owned = self.manager.current
        stops = []
        try:
            with patch("starlight_voice.component_session._STARTUP_CLOSE_SECONDS", 0.01, create=True):
                stops = [asyncio.create_task(self.manager.stop(self.identity(), self.scope["capability"]))
                         for _ in range(3)]
                await cancelling.wait()
                cancelled_waiter = stops.pop()
                cancelled_waiter.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await cancelled_waiter
                done, pending = await asyncio.wait(stops, timeout=1)
                self.assertFalse(pending, "Stop must return retry while owned startup cancellation is pending")
                for task in done:
                    with self.assertRaises(BridgeError) as held:
                        task.result()
                    self.assertEqual(held.exception.status, 503)
                self.assertIs(self.manager.current, owned)
                self.assertTrue(owned.cleanup_task.done())
                self.assertFalse(owned.offer_task.done())
                self.assertEqual(self.resources[0].close_calls, 0)
                with self.assertRaises(BridgeError):
                    self.bridge.authorize(self.identity(), self.scope["capability"])
                self.clock[0] = 301
                with self.assertRaises(BridgeError) as slot_held:
                    await self.bridge.start({"provider": "grok", "voice": "ara"})
                self.assertEqual(slot_held.exception.status, 429)
                with self.assertRaises(BridgeError):
                    await self.manager.stop(self.identity(), self.scope["capability"])
                self.assertEqual(cancellations, [True])
                self.assertFalse(owned.offer_task.done(), "Retry must not cancel startup cleanup a second time")
        finally:
            release.set()
            await asyncio.gather(waiter, *stops, return_exceptions=True)
        result = await self.manager.stop(self.identity(), self.scope["capability"])
        self.assertTrue(result["runtime_closed"])
        self.assertEqual(self.resources[0].close_calls, 1)
        self.assertIsNone(self.bridge.session)
        native = await self.bridge.start({"provider": "grok", "voice": "ara"})
        self.bridge.stop({"session_id": native["session_id"]}, native["capability"])

    async def test_cancelled_last_stop_waiter_does_not_leave_an_unobserved_cleanup_failure(self):
        await self.manager.offer(self.offer(), self.scope["capability"])
        resource = self.resources[0]
        resource.close_failures = 1
        entered, release = asyncio.Event(), asyncio.Event()
        original_close = resource.close

        async def delayed_close():
            entered.set()
            await release.wait()
            await original_close()

        resource.close = delayed_close
        loop = asyncio.get_running_loop()
        contexts = []
        previous_handler = loop.get_exception_handler()
        loop.set_exception_handler(lambda loop, context: contexts.append(context))
        waiter = asyncio.create_task(self.manager.stop(self.identity(), self.scope["capability"]))
        try:
            await entered.wait()
            cleanup = self.manager.current.cleanup_task
            waiter.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await waiter
            release.set()
            done, pending = await asyncio.wait([cleanup], timeout=1)
            self.assertFalse(pending)
            self.assertIsNotNone(self.bridge.session)
            self.assertTrue((await self.manager.stop(self.identity(), self.scope["capability"]))["runtime_closed"])
            done.clear()
            del cleanup
            gc.collect()
            self.assertEqual(contexts, [], "A disconnected Stop caller must not expose private cleanup exceptions")
        finally:
            release.set()
            await asyncio.gather(waiter, return_exceptions=True)
            loop.set_exception_handler(previous_handler)

    async def test_component_tools_reuse_read_scope_and_cannot_release_runtime_slot(self):
        reads = ComponentReadTools(bridge=self.bridge, scope=self.scope)
        reads.start()
        self.assertEqual(reads.scope["session_id"], self.bridge.session.session_id)
        await reads.stop()
        self.assertIsNotNone(self.bridge.session)
        with self.assertRaises(BridgeError):
            reads.start()

    async def test_disconnected_runtime_finishes_cleanup_before_new_native_start(self):
        await self.manager.offer(self.offer(), self.scope["capability"])
        self.resources[0].ended()
        cleanup = self.manager.current.cleanup_task
        await cleanup
        self.assertIsNone(self.bridge.session)
        native = await self.bridge.start({"provider": "grok", "voice": "ara"})
        self.assertEqual(self.mints, ["grok"])
        self.bridge.stop({"session_id": native["session_id"]}, native["capability"])

    async def test_expiry_releases_a_reserved_session_without_starting_media(self):
        await self.manager.close()
        self.manager.lifetime = 1
        await self.manager.start({"provider": "elevenlabs", "voice": "fixture-voice"})
        expiry = self.manager.current.expiry_task
        await asyncio.wait_for(expiry, 2)
        self.assertIsNone(self.bridge.session)
        self.assertEqual(self.resources, [])


class ComponentValidationTests(unittest.TestCase):
    def test_grok_profile_needs_only_dedicated_key_and_advertises_its_actual_engine(self):
        with patch.dict(os.environ, {"STARLIGHT_VOICE_PROFILE": "grok-openrouter",
                                     "STARLIGHT_VOICE_OPENROUTER_API_KEY": "fixture"}, clear=True):
            spec = component_spec(present=lambda name: name != "groq", sdk_ready=lambda: True)
        self.assertTrue(spec["configured"])
        self.assertEqual(spec["label"], "Grok via OpenRouter")
        self.assertEqual(spec["voices"], ["ara"])
        self.assertFalse(spec["runtime_verified"])

    def test_broken_dedicated_key_or_unsupported_grok_voice_is_not_offered_as_ready(self):
        for key, voice in (("", "ara"), ("*not found*", "ara"), ("fixture", "george")):
            with patch.dict(os.environ, {"STARLIGHT_VOICE_PROFILE": "grok-openrouter",
                                         "STARLIGHT_VOICE_OPENROUTER_API_KEY": key,
                                         "OPENROUTER_API_KEY": "shared", "STARLIGHT_TTS_VOICE_ID": voice}, clear=True):
                spec = component_spec(present=lambda name: True, sdk_ready=lambda: True)
            self.assertFalse(spec["configured"])

    def test_browser_requires_an_sdk_with_public_detector_cleanup(self):
        keys = {key: "fixture" for key in ("GROQ_API_KEY", "OPENROUTER_API_KEY", "ELEVENLABS_API_KEY")}
        with patch.dict(os.environ, keys, clear=True):
            for ready in (False, True):
                spec = component_spec(Settings(tts_voice_id="fixture-voice"), present=lambda name: True,
                                      sdk_ready=lambda ready=ready: ready)
                self.assertEqual(spec["configured"], ready)
                self.assertFalse(spec["runtime_verified"])

    def test_discovery_checks_presence_without_importing_or_loading_a_runtime(self):
        probes = []
        with patch.dict(os.environ, {}, clear=True):
            spec = component_spec(Settings(tts_voice_id="fixture-voice"),
                                  present=lambda name: probes.append(name) or False)
        self.assertFalse(spec["configured"])
        self.assertFalse(spec["runtime_verified"])
        self.assertEqual(probes, [])

    def test_missing_or_hidden_control_voice_is_not_offered(self):
        for voice in ("", "private\nvoice", "x" * 129):
            spec = component_spec(Settings(tts_voice_id=voice), present=lambda name: False)
            self.assertEqual(spec["voices"], [])
            self.assertFalse(spec["configured"])

    def test_unknown_sdp_fields_oversized_content_and_candidate_flood_are_refused(self):
        invalid = [
            {"session_id": "fixture", "sdp": SDP, "type": "offer", "restart_pc": True},
            {"session_id": "fixture", "sdp": SDP * 1000, "type": "offer"},
            {"session_id": "fixture", "sdp": SDP + "a=candidate:fixture\r\n" * 65, "type": "offer"},
            {"session_id": "fixture", "sdp": SDP + "\x00", "type": "offer"},
        ]
        for payload in invalid:
            with self.assertRaises(BridgeError):
                validate_offer(payload)


if __name__ == "__main__":
    unittest.main()
