"""Minimal AG-UI transport: HTTP + Server-Sent Events, standard library only.

The event contract lives in `agui_events.py`. This module puts it on a socket so
a client (CopilotKit, the tray, OpenBot, curl) can consume a real stream. No
FastAPI, no uvicorn, no model, no key - which matters on a machine with 5 GiB
free and a rule against adding runtimes.

Endpoints:
    GET  /health   -> {"ok": true, "transport": "sse", "schema": ...}
    POST /agui     -> text/event-stream of AG-UI events for one run

Contract:
    request  {"threadId": str, "runId": str, "message": str}
    response one `data: <json>` line per event, blank-line separated

Deliberately not here yet:
  * audio frames - voice is a separate transport that feeds the same events
  * persistence - a run is stateless until the memory decision lands
  * auth - it binds to localhost only; do not expose it until there is a token
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from . import agui_events as agui


def status_reply(message: str, grounding: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """A deterministic, honest reply. No model, no execution, no claims."""
    text = (message or "").strip()
    if not text:
        body = "No utterance received."
    else:
        body = (
            "Bounded router: this transport is live and the event stream is real. "
            "Reasoning is not wired yet, so nothing was interpreted or executed."
        )
    return {
        "reply": body,
        "detectedLanguage": "en",
        "proposedTask": None,
        "mode": "bounded-status-router",
        "grounding": dict(grounding or {}),
    }


class Runtime:
    """Turns a request into an ordered event stream."""

    def __init__(self, reply_fn: Callable[[str], dict[str, Any]] = status_reply):
        self.reply_fn = reply_fn

    def handle(self, payload: Mapping[str, Any]) -> list[dict[str, Any]]:
        thread_id = payload.get("threadId")
        run_id = payload.get("runId")
        message = payload.get("message", "")
        if not isinstance(thread_id, str) or not thread_id:
            raise ValueError("threadId is required")
        if not isinstance(run_id, str) or not run_id:
            raise ValueError("runId is required")
        if not isinstance(message, str):
            raise ValueError("message must be a string")

        reply = self.reply_fn(message)
        events = agui.reply_to_events(reply, thread_id=thread_id, run_id=run_id)
        problems = agui.validate_stream(events)
        if problems:
            # A producer that emits an invalid stream must fail loudly here, not
            # in a client that has to guess what the broken event meant.
            raise RuntimeError(f"invalid event stream: {problems}")
        return events


def make_handler(runtime: Runtime) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):  # keep test output clean
            return

        def _json(self, status: int, payload: Mapping[str, Any]) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802 - stdlib naming
            if self.path == "/health":
                self._json(200, {"ok": True, "transport": "sse", "schema": agui.SCHEMA_VERSION})
                return
            self._json(404, {"error": "not found"})

        def do_POST(self):  # noqa: N802 - stdlib naming
            # Read the body before routing. Leaving an unread body on a keep-alive
            # connection makes the client see a connection reset instead of a 404.
            length = int(self.headers.get("Content-Length") or 0)
            raw_body = self.rfile.read(length) if length else b""

            if self.path != "/agui":
                self._json(404, {"error": "not found"})
                return

            try:
                payload = json.loads(raw_body or b"{}")
            except json.JSONDecodeError:
                self._json(400, {"error": "body must be JSON"})
                return

            try:
                events = runtime.handle(payload)
            except ValueError as exc:
                self._json(400, {"error": str(exc)})
                return
            except RuntimeError as exc:
                self._json(500, {"error": str(exc)})
                return

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            for event in events:
                self.wfile.write(f"data: {json.dumps(event)}\n\n".encode())
            self.wfile.flush()
            self.close_connection = True

    return Handler


def serve(host: str = "127.0.0.1", port: int = 8788, runtime: Runtime | None = None) -> ThreadingHTTPServer:
    """Create a server. Call serve_forever() for a long-running process."""
    return ThreadingHTTPServer((host, port), make_handler(runtime or Runtime()))


def main() -> int:  # pragma: no cover - exercised by hand, not by tests
    import argparse

    parser = argparse.ArgumentParser(description="Starlight AG-UI transport")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8788)
    args = parser.parse_args()

    httpd = serve(args.host, args.port)
    host, port = httpd.server_address[:2]
    print(f"ag-ui transport on http://{host}:{port}  (POST /agui, GET /health)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
