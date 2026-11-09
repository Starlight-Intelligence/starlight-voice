"""Lazy SmallWebRTC binding for the existing component graph, with owned cleanup."""

from __future__ import annotations

import asyncio


class ComponentWebRTC:
    def __init__(self, settings, bridge, scope, ended):
        self.settings, self.bridge, self.scope, self.ended = settings, bridge, scope, ended
        self.peer = self.worker = self.task = self.pipeline = None
        self.cleanup_jobs = {}
        self.construction_resources = []
        self.closed = False

    async def initialize(self, sdp):
        from pipecat.frames.frames import LLMFullResponseStartFrame, LLMTextFrame, StartFrame
        from pipecat.pipeline.pipeline import Pipeline
        from pipecat.pipeline.runner import PipelineRunner
        from pipecat.pipeline.worker import PipelineWorker
        from pipecat.processors.frame_processor import FrameProcessor
        from pipecat.transports.base_transport import TransportParams
        from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
        from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport

        from .voice_loop import build_graph

        self.peer = SmallWebRTCConnection(ice_servers=[])

        @self.peer.event_handler("closed")
        async def disconnected(peer):
            if not self.closed:
                self.ended()

        await self.peer.initialize(sdp=sdp, type="offer")
        transport = SmallWebRTCTransport(self.peer, TransportParams(audio_in_enabled=True, audio_out_enabled=True))
        ready = asyncio.Event()
        peer = self.peer

        class Replies(FrameProcessor):
            async def process_frame(self, frame, direction):
                await super().process_frame(frame, direction)
                if isinstance(frame, LLMFullResponseStartFrame):
                    peer.send_app_message({"type": "component.caption", "reset": True})
                elif isinstance(frame, LLMTextFrame):
                    peer.send_app_message({"type": "component.caption", "text": frame.text[:4096]})
                await self.push_frame(frame, direction)

        class Ready(FrameProcessor):
            async def process_frame(self, frame, direction):
                await super().process_frame(frame, direction)
                if isinstance(frame, StartFrame):
                    ready.set()
                await self.push_frame(frame, direction)

        pipeline, _ = build_graph(self.settings, with_transport=False, transport=transport,
                                  read_bridge=self.bridge, read_scope=self.scope, reply_observer=Replies(),
                                  own_resource=self.construction_resources.append)
        self.pipeline = pipeline
        self.worker = PipelineWorker(Pipeline([pipeline, Ready()]), enable_rtvi=False)
        self.task = asyncio.create_task(PipelineRunner(handle_sigint=False).run(self.worker))

        def completed(task):
            if not task.cancelled():
                task.exception()
            if not self.closed:
                self.ended()

        self.task.add_done_callback(completed)
        await asyncio.wait_for(ready.wait(), 5)
        return self.peer.get_answer()

    async def close(self):
        self.closed = True
        async def close_worker():
            async with asyncio.timeout(3):
                if self.worker is not None and self.task is not None:
                    if not self.task.done():
                        await self.worker.cancel()
                    try:
                        await asyncio.shield(self.task)
                    except asyncio.CancelledError:
                        if not self.task.cancelled():
                            raise
                    except Exception:
                        # A failed worker still needs explicit processor cleanup.
                        pass
                if self.pipeline is not None:
                    await self.pipeline.cleanup()
                    self.construction_resources.clear()
                else:
                    # Construction can fail after earlier services have allocated resources.
                    failed = False
                    for resource in list(reversed(self.construction_resources)):
                        try:
                            await resource.cleanup()
                            self.construction_resources.remove(resource)
                        except Exception:
                            failed = True
                    if failed:
                        raise RuntimeError("Partial component graph cleanup incomplete")
                self.worker = self.task = self.pipeline = None

        async def close_peer():
            async with asyncio.timeout(3):
                if self.peer is not None:
                    await self.peer.disconnect()
                    self.peer = None

        for name, closer in (("worker", close_worker), ("peer", close_peer)):
            job = self.cleanup_jobs.get(name)
            if job is None or job.done() and (job.cancelled() or job.exception() is not None):
                self.cleanup_jobs[name] = asyncio.create_task(closer())
        results = await asyncio.gather(*(asyncio.shield(job) for job in self.cleanup_jobs.values()),
                                       return_exceptions=True)
        if any(isinstance(result, BaseException) for result in results):
            raise RuntimeError("Owned component resources have not finished cleanup")
