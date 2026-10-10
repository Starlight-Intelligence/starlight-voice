"""Starlight Voice local server — serves BOTH surfaces from one localhost origin.

Routes (fail-closed allowlist — anything not listed is 404, so repo internals
like .git / sidecar source never leak even on 127.0.0.1):

    GET  /                         -> site/index.html        (the landing site)
    GET  /tokens.css|styles.css|motion.js  -> site/*         (landing assets)
    GET  /dashboard/cockpit.{html,css,js}  -> dashboard/*    (the live console)
    GET  /status                   -> system_status()        (cockpit data source)
    GET  /healthz                  -> liveness probe
    POST /ratings                  -> append a bake-off rating (64KB DoS-guarded)

This mirrors the default-DENY doctrine in cognition/dispatch.py: enumerate what
is allowed, refuse the rest — rather than blocklisting what is dangerous.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent  # repo root (serves site/ + dashboard/)
DASH = ROOT / "dashboard"
SITE = ROOT / "site"
RATINGS = DASH / "ratings.jsonl"
MAX_BODY = 64 * 1024  # reject oversized POSTs (memory-DoS guard)
sys.path.insert(0, str(ROOT / "sidecar" / "src"))  # for /status -> system_status()

# Explicit GET allowlist: request path -> (file on disk, content-type). Fail-closed.
_CT_HTML = "text/html; charset=utf-8"
_CT_CSS = "text/css; charset=utf-8"
_CT_JS = "text/javascript; charset=utf-8"
STATIC: dict[str, tuple[Path, str]] = {
    "/": (SITE / "index.html", _CT_HTML),
    "/index.html": (SITE / "index.html", _CT_HTML),
    "/tokens.css": (SITE / "tokens.css", _CT_CSS),
    "/styles.css": (SITE / "styles.css", _CT_CSS),
    "/motion.js": (SITE / "motion.js", _CT_JS),
    "/dashboard/cockpit.html": (DASH / "cockpit.html", _CT_HTML),
    "/dashboard/cockpit.css": (DASH / "cockpit.css", _CT_CSS),
    "/dashboard/cockpit.js": (DASH / "cockpit.js", _CT_JS),
    "/dashboard/native-conversation.mjs": (DASH / "native-conversation.mjs", _CT_JS),
    "/dashboard/pcm-capture.js": (DASH / "pcm-capture.js", _CT_JS),
}


def trusted_host(host: str, port: int) -> bool:
    return host in {f"127.0.0.1:{port}", f"localhost:{port}"}


def trusted_voice_request(headers, port: int) -> bool:
    host = headers.get("Host", "")
    return (
        trusted_host(host, port) and headers.get("Origin") == f"http://{host}"
        and headers.get("Content-Type", "").lower() == "application/json"
        and not headers.get("Transfer-Encoding")
        and headers.get("Sec-Fetch-Site", "same-origin") == "same-origin"
    )


def _system_status() -> dict:
    """Live operator state for the console. Degrades to an error dict if the sidecar is absent."""
    try:
        from starlight_voice.status import system_status

        return system_status()
    except Exception as e:  # noqa: BLE001 - console must render even if the sidecar is absent
        return {"error": f"{type(e).__name__}: {e}", "settings": {}, "adapters": {}, "variants": [], "runs": []}


class DashboardHandler(BaseHTTPRequestHandler):
    def end_headers(self) -> None:  # security headers on every response
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Permissions-Policy", "microphone=(self), camera=()")
        self.send_header("Cache-Control", "no-store")
        if self.path.startswith("/dashboard/"):
            self.send_header("Content-Security-Policy", (
                "default-src 'self'; script-src 'self'; style-src 'self' https://fonts.googleapis.com; "
                "font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; "
                "connect-src 'self' wss://api.x.ai https://api.openai.com; media-src 'self' blob:; "
                "worker-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
            ))
        super().end_headers()

    def log_message(self, *args) -> None:  # quiet by default
        pass

    def do_GET(self) -> None:
        if not trusted_host(self.headers.get("Host", ""), self.server.server_address[1]):
            self.send_error(403, "Untrusted host")
            return
        path = urlparse(self.path).path
        if path == "/voice/capabilities":
            self._json(200, self.server.voice_bridge.capabilities())
            return
        if path == "/healthz":
            self._json(200, {"ok": True, "service": "starlight-voice-dashboard"})
            return
        if path == "/status":
            self._json(200, _system_status())
            return
        entry = STATIC.get(path)
        if entry is None:
            self.send_error(404, "Not found")
            return
        self._serve_file(*entry)

    def do_POST(self) -> None:
        if urlparse(self.path).path in {"/voice/session", "/voice/tool", "/voice/stop"}:
            self._voice_post()
            return
        if urlparse(self.path).path != "/ratings":
            self.send_error(404, "Not found")
            return

        try:
            length = int(self.headers.get("content-length", "0"))
        except ValueError:
            self.send_error(400, "Bad content-length")
            return
        if length <= 0 or length > MAX_BODY:
            self.close_connection = True  # don't read the oversized body; signal close cleanly
            self.send_error(413, "Payload too large")
            return

        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self.send_error(400, "Invalid JSON")
            return

        record = {
            "recorded_at": datetime.now(UTC).isoformat(),
            "source": "starlight-voice-dashboard",
            "payload": payload,
        }
        try:
            with RATINGS.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as e:
            self._json(500, {"ok": False, "error": f"write failed: {e}"})
            return

        self._json(200, {"ok": True, "path": str(RATINGS)})

    def _voice_post(self) -> None:
        import asyncio

        from starlight_voice.native_bridge import BridgeError

        if not trusted_voice_request(self.headers, self.server.server_address[1]):
            self._json(403, {"error": "Voice requests require the exact local origin and JSON content type"})
            self.close_connection = True
            return
        if not self.server.voice_slots.acquire(blocking=False):
            self._json(429, {"error": "Voice broker is busy"})
            self.close_connection = True
            return
        try:
            lengths = self.headers.get_all("Content-Length", [])
            if len(lengths) != 1 or not lengths[0].isdigit() or not 0 < int(lengths[0]) <= 8192:
                self.close_connection = True
                raise BridgeError("Voice request exceeds the body limit", 413)
            self.connection.settimeout(3)
            raw = self.rfile.read(int(lengths[0]))
            if len(raw) != int(lengths[0]):
                raise BridgeError("Voice request is incomplete")
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise BridgeError("A JSON object is required")
            capability = self.headers.get("X-Voice-Capability", "")
            bridge = self.server.voice_bridge
            path = urlparse(self.path).path
            if path == "/voice/session":
                result = asyncio.run(bridge.start(payload))
            elif path == "/voice/tool":
                result = asyncio.run(bridge.tool(payload, capability))
            else:
                result = bridge.stop(payload, capability)
            self._json(200, result)
        except BridgeError as error:
            self._json(error.status, {"error": str(error)})
        except (ValueError, UnicodeDecodeError):
            self._json(400, {"error": "Invalid JSON"})
        except Exception:  # noqa: BLE001 - redact infrastructure failures at the browser boundary
            self._json(503, {"error": "Voice request unavailable"})
        finally:
            self.server.voice_slots.release()

    def _serve_file(self, file_path: Path, content_type: str) -> None:
        try:
            body = file_path.read_bytes()
        except OSError:
            self.send_error(404, "Not found")
            return
        self.send_response(200)
        self.send_header("content-type", content_type)
        self.send_header("content-length", str(len(body)))
        self.send_header("cache-control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, obj: dict) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def make_server(port: int | None = None) -> ThreadingHTTPServer:
    from starlight_voice.native_bridge import NativeBridge

    port = port if port is not None else int(os.environ.get("STARLIGHT_DASHBOARD_PORT", "8765"))
    server = ThreadingHTTPServer(("127.0.0.1", port), DashboardHandler)
    server.voice_bridge = NativeBridge()
    server.voice_slots = threading.BoundedSemaphore(4)
    return server


def main() -> None:
    from starlight_voice.config import load_local_env

    load_local_env()
    server = make_server()
    host, port = server.server_address
    print(f"Starlight Voice: http://{host}:{port}  (console at /dashboard/cockpit.html)")
    server.serve_forever()


if __name__ == "__main__":
    main()
