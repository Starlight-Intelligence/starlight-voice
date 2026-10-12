"""Demand-created async host inside the existing dashboard server, never a daemon service."""

from __future__ import annotations

import asyncio
import concurrent.futures
import threading

from .component_session import ComponentSessions, component_spec
from .native_bridge import BridgeError


class ComponentHost:
    def __init__(self, bridge, *, manager_factory=ComponentSessions, descriptor=component_spec):
        self.bridge, self.manager_factory = bridge, manager_factory
        self.descriptor = descriptor
        self.lock = threading.Lock()
        self.loop = self.thread = self.manager = None
        self.ready = threading.Event()
        self.closing = False
        self.closed = False

    def capabilities(self):
        return self.descriptor()

    def _run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            self.loop = loop
            self.manager = self.manager_factory(self.bridge)
            self.ready.set()
            loop.run_forever()
        finally:
            self.ready.set()
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.run_until_complete(loop.shutdown_default_executor())
            loop.close()

    def _ensure(self):
        with self.lock:
            if self.closing:
                raise BridgeError("Component host is closing", 503)
            if self.thread is None:
                self.thread = threading.Thread(target=self._run, name="voice-component-owned-loop", daemon=True)
                self.thread.start()
            thread = self.thread
        if (not self.ready.wait(3) or not thread.is_alive() or self.loop is None
                or self.manager is None or self.loop.is_closed()):
            raise BridgeError("Component host unavailable; its existing owner must reconcile cleanup", 503)

    def call(self, action, payload=None, capability=None):
        if action not in {"start", "offer", "stop"}:
            raise BridgeError("Unknown component action")
        if action == "start":
            spec = self.capabilities()
            if (not isinstance(payload, dict) or set(payload) != {"provider", "voice"}
                    or payload["provider"] != "elevenlabs"):
                raise BridgeError("Choose only the component provider and configured voice")
            if not spec["configured"] or payload["voice"] not in spec["voices"]:
                raise BridgeError("Component voice needs configured dependencies, keys and an account voice", 503)
        elif self.thread is None:
            raise BridgeError("No component session has started", 401)
        self._ensure()
        method = getattr(self.manager, action)
        coroutine = method(payload) if action == "start" else method(payload, capability)
        future = asyncio.run_coroutine_threadsafe(coroutine, self.loop)
        try:
            return future.result(timeout=12)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise BridgeError("Component request timed out; Stop before reconnecting", 503) from None

    def try_stop(self, payload, capability):
        if self.thread is None:
            return None

        async def stop_if_owned():
            current = self.manager.current
            last = self.manager.last_stopped
            identity = payload.get("session_id") if isinstance(payload, dict) else None
            if identity is not None and (current and current.scope["session_id"] == identity
                                         or last and last[0] == identity):
                return await self.manager.stop(payload, capability)
            return None

        self._ensure()
        future = asyncio.run_coroutine_threadsafe(stop_if_owned(), self.loop)
        try:
            return future.result(timeout=8)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise BridgeError("Component cleanup timed out; retry Stop", 503) from None

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.closing = True
            thread, loop, manager = self.thread, self.loop, self.manager
        if thread is None:
            self.closed = True
            return
        if not thread.is_alive() or loop is None or loop.is_closed() or manager is None:
            raise BridgeError("Component host stopped without a reconciled cleanup receipt", 503)
        future = asyncio.run_coroutine_threadsafe(manager.close(), loop)
        try:
            future.result(timeout=8)
        except Exception:
            future.cancel()
            # Retain the exact loop and handles for a later owner retry.
            raise BridgeError("Component host cleanup incomplete; retry shutdown", 503) from None
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=5)
        if thread.is_alive():
            raise BridgeError("Component host thread has not finished shutdown", 503)
        self.closed = True
