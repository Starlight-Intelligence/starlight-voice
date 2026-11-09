"""Pure owned-resource cleanup tests; the real peer is covered on hosted SDK CI."""

import asyncio
import unittest

from starlight_voice.component_webrtc import ComponentWebRTC


class Resource:
    def __init__(self, *, fails=False):
        self.fails = fails
        self.calls = 0

    async def cleanup(self):
        self.calls += 1
        if self.fails:
            raise RuntimeError("private provider detail")

    async def disconnect(self):
        await self.cleanup()


class ComponentWebRTCCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_peer_cleanup_does_not_prevent_worker_cleanup_and_can_retry(self):
        runtime = ComponentWebRTC(None, None, None, lambda: None)
        peer, pipeline = Resource(fails=True), Resource()
        runtime.peer, runtime.pipeline = peer, pipeline
        with self.assertRaisesRegex(RuntimeError, "Owned component"):
            await runtime.close()
        self.assertIsNone(runtime.pipeline)
        self.assertIs(runtime.peer, peer)
        self.assertEqual(pipeline.calls, 1)
        peer.fails = False
        await runtime.close()
        self.assertIsNone(runtime.peer)
        self.assertEqual((peer.calls, pipeline.calls), (2, 1))

    async def test_partial_graph_failure_cleans_independent_resources_and_retains_failed_handle(self):
        runtime = ComponentWebRTC(None, None, None, lambda: None)
        failed, successful = Resource(fails=True), Resource()
        runtime.construction_resources = [failed, successful]
        with self.assertRaises(RuntimeError):
            await runtime.close()
        self.assertEqual(runtime.construction_resources, [failed])
        self.assertEqual(successful.calls, 1)
        failed.fails = False
        await runtime.close()
        self.assertEqual(runtime.construction_resources, [])
        self.assertEqual((failed.calls, successful.calls), (2, 1))

    async def test_cancelled_worker_task_still_releases_processor_resources(self):
        runtime = ComponentWebRTC(None, None, None, lambda: None)
        runtime.worker = object()
        runtime.task = asyncio.create_task(asyncio.sleep(100))
        runtime.task.cancel()
        await asyncio.gather(runtime.task, return_exceptions=True)
        pipeline = Resource()
        runtime.pipeline = pipeline
        await runtime.close()
        self.assertEqual(pipeline.calls, 1)
        self.assertIsNone(runtime.worker)
        self.assertIsNone(runtime.task)


if __name__ == "__main__":
    unittest.main()
