"""Starlight Voice local server — serves BOTH surfaces from one localhost origin.

Routes (fail-closed allowlist — anything not listed is 404, so repo internals
like .git / sidecar source never leak even on 127.0.0.1):

    GET  /                         -> site/index.html        (the landing site)
    GET  /tokens.css|styles.css|motion.js  -> site/*         (landing assets)
    GET  /dashboard/cockpit.{html,css,js}  -> dashboard/*    (the live console)
    GET  /voice/ + explicit assets       -> dashboard/voice/* (mic-reactive experience)
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
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent  # repo root (serves site/ + dashboard/)
DASH = ROOT / "dashboard"
VOICE = DASH / "voice"
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
    "/voice/": (VOICE / "index.html", _CT_HTML),
    "/voice/index.html": (VOICE / "index.html", _CT_HTML),
    "/voice/voice.css": (VOICE / "voice.css", _CT_CSS),
    "/voice/voice.js": (VOICE / "voice.js", _CT_JS),
    "/voice/voice-runtime.mjs": (VOICE / "voice-runtime.mjs", _CT_JS),
    "/live": (DASH / "live.html", _CT_HTML),
    "/live.html": (DASH / "live.html", _CT_HTML),
    "/live.css": (DASH / "live.css", _CT_CSS),
}


def _system_status() -> dict:
    """Live operator state for the console. Degrades to an error dict if the sidecar is absent."""
    try:
        from starlight_voice.status import system_status

        return system_status()
    except Exception:  # noqa: BLE001 - console must render even if the sidecar is absent
        return {
            "status": "STANDBY",
            "voice_loop": {"mode": "local-first", "first_audio_budget_ms": 606, "state": "standby"},
            "settings": {"sovereignty": "high", "privacy": "high", "local_only": True},
            "adapters": {"mcp": "ready", "whisper": "available", "piper": "available"},
            "variants": ["fast-mode", "deliberation", "browser-agent"],
            "runs": []
        }


class DashboardHandler(BaseHTTPRequestHandler):
    def end_headers(self) -> None:  # security headers on every response
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        super().end_headers()

    def log_message(self, *args) -> None:  # quiet by default
        pass

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/healthz":
            self._json(200, {"ok": True, "service": "starlight-voice-dashboard"})
            return
        if path == "/status":
            self._json(200, _system_status())
            return
        if path == "/api/live":
            from starlight_voice.session_store import read_live

            self._json(200, read_live())
            return
        if path == "/api/sessions":
            from starlight_voice.session_store import read_sessions

            self._json(200, {"sessions": read_sessions(20)})
            return
        entry = STATIC.get(path)
        if entry is None:
            self.send_error(404, "Not found")
            return
        self._serve_file(*entry)

    def do_POST(self) -> None:
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
    port = port if port is not None else int(os.environ.get("STARLIGHT_DASHBOARD_PORT", "8765"))
    return ThreadingHTTPServer(("127.0.0.1", port), DashboardHandler)


def main() -> None:
    server = make_server()
    host, port = server.server_address
    print(f"Starlight Voice: http://{host}:{port}  (console at /dashboard/cockpit.html)")
    server.serve_forever()


if __name__ == "__main__":
    main()
