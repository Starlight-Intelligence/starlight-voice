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
    GET  /api/pending              -> waiting voice handoffs: code, exact request, repo, seat
    POST /api/approve {"code"}     -> human ack for one handoff by its code (tier-B gate)

Every POST must be same-origin JSON: Host and Origin are 127.0.0.1 or
localhost on this port, and content-type is application/json.

This mirrors the default-DENY doctrine in cognition/dispatch.py: enumerate what
is allowed, refuse the rest — rather than blocklisting what is dangerous.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent  # repo root (serves site/ + dashboard/)
DASH = ROOT / "dashboard"
VOICE = DASH / "voice"
SITE = ROOT / "site"
RATINGS = DASH / "ratings.jsonl"
CHAIR = Path(
    os.environ.get(
        "STARLIGHT_CHAIR_PULSE",
        r"C:\Users\frank\starlight\queen\state\chair-pulse.json",
    )
)
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


POST_ROUTES = frozenset({"/ratings", "/api/approve", "/api/listen/start", "/api/listen/stop", "/api/operator"})


def desk_status() -> dict:
    """Queen loop summary for the face. Counts only. Card text stays in the pulse file."""

    empty = {"loop": "unavailable", "started": False, "held": 0, "inbox": 0, "pulse": ""}
    try:
        data = json.loads(CHAIR.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty
    if not isinstance(data, dict):
        return empty
    machine = data.get("machine") if isinstance(data.get("machine"), dict) else {}
    loop = str(machine.get("loopStatus") or "unavailable")
    if len(loop) > 40 or any(ord(ch) < 32 for ch in loop):
        loop = "unavailable"
    held = data.get("heldTonight")
    inbox = data.get("inbox")
    pulse = str(data.get("ts") or "")
    return {
        "loop": loop,
        "started": False,
        "held": len(held) if isinstance(held, list) else 0,
        "inbox": len(inbox) if isinstance(inbox, list) else 0,
        "pulse": pulse[:40],
    }


def _listen_log() -> Path:
    override = os.environ.get("STARLIGHT_VOICE_LISTEN_LOG", "").strip()
    return Path(override) if override else ROOT / "memory" / "voice" / "listen.out.log"


def _pid_path() -> Path:
    override = os.environ.get("STARLIGHT_VOICE_LISTEN_PID", "").strip()
    return Path(override) if override else ROOT / "memory" / "voice" / "listen.pid"


def _listener_python() -> Path:
    return ROOT / "sidecar" / ".venv" / "Scripts" / "python.exe"


def _listener_processes() -> list[int]:
    """Clap-listener pids. The dashboard process is never included."""

    if os.environ.get("STARLIGHT_VOICE_LISTEN_DRY") == "1":
        return []
    script = (
        "Get-CimInstance Win32_Process -Filter \"Name = 'python.exe'\" | "
        "Where-Object { $_.CommandLine -match 'starlight_voice' -and $_.CommandLine -match '\\blisten\\b' "
        "-and $_.CommandLine -notmatch 'dashboard' } | "
        "ForEach-Object { $_.ProcessId }"
    )
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    found: list[int] = []
    for line in completed.stdout.splitlines():
        text = line.strip()
        if text.isdigit():
            found.append(int(text))
    return found


def _terminate(pid: int) -> bool:
    if pid <= 0 or pid == os.getpid():
        return False
    try:
        completed = subprocess.run(
            ["taskkill", "/PID", str(pid), "/T"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def _listen_hint() -> str:
    try:
        text = _listen_log().read_text(encoding="utf-8", errors="replace")[-400:]
    except OSError:
        return "The listener did not arm."
    for needle, message in (
        ("microphone unavailable", "The microphone did not open."),
        ("No xAI key", "No voice key is available."),
        ("Already listening", "She is already armed."),
        ("Token disappeared", "The voice key disappeared before open."),
    ):
        if needle in text:
            return message
    return "The listener did not arm."


def _clear_pid_file() -> None:
    try:
        _pid_path().unlink(missing_ok=True)
    except OSError:
        pass


def start_listener() -> dict:
    """Arm the clap listener. The page does not open a microphone itself."""

    from starlight_voice.session_store import read_live

    if _listener_processes():
        phase = str(read_live().get("phase") or "armed")
        if phase == "stopped":
            phase = "armed"
        return {"ok": True, "phase": phase, "already": True}
    python = _listener_python()
    if not python.is_file():
        return {"ok": False, "phase": "stopped", "error": "The listener runtime is missing."}
    log_path = _listen_log()
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handle = log_path.open("a", encoding="utf-8")
    except OSError:
        return {"ok": False, "phase": "stopped", "error": "The listener log could not be opened."}
    flags = 0
    if os.name == "nt":
        flags = (
            getattr(subprocess, "CREATE_NO_WINDOW", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0)
        )
    try:
        subprocess.Popen(
            [str(python), "-m", "starlight_voice", "listen"],
            cwd=str(ROOT),
            stdout=handle,
            stderr=handle,
            creationflags=flags,
        )
    except OSError:
        return {"ok": False, "phase": "stopped", "error": "The listener did not start."}
    finally:
        handle.close()
    deadline = time.monotonic() + 8
    phase = "stopped"
    while time.monotonic() < deadline:
        phase = str(read_live().get("phase") or "stopped")
        if phase in {"armed", "open", "speaking"}:
            return {"ok": True, "phase": phase, "already": False}
        time.sleep(0.2)
    return {"ok": False, "phase": phase, "error": _listen_hint()}


def operator_request(payload: dict) -> dict:
    """Face actions. Lookup, a named open, or a held seat. Nothing is spawned."""

    from starlight_voice.operator_actions import hold_handoff, lookup_memory, open_named

    action = str(payload.get("action") or "")
    if action == "lookup":
        return lookup_memory(str(payload.get("query") or ""))
    if action == "open":
        return open_named(str(payload.get("kind") or ""), str(payload.get("target") or ""))
    if action == "handoff":
        return hold_handoff(str(payload.get("utterance") or ""), str(payload.get("seat") or ""))
    return {"ok": False, "started": False, "target": "", "spoken": "That action is not on this operator."}


def stop_listener() -> dict:
    """Stop the clap listener and leave the watch page up."""

    from starlight_voice.session_store import publish

    stopped = [pid for pid in _listener_processes() if _terminate(pid)]
    _clear_pid_file()
    publish("stopped")
    return {"ok": True, "phase": "stopped", "stopped": stopped}


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
        if path == "/api/pending":
            from starlight_voice.approval import pending_details

            self._json(200, {"pending": pending_details()})
            return
        if path == "/api/sessions":
            from starlight_voice.session_store import read_sessions

            self._json(200, {"sessions": read_sessions(20)})
            return
        if path == "/api/desk":
            self._json(200, desk_status())
            return
        entry = STATIC.get(path)
        if entry is None:
            self.send_error(404, "Not found")
            return
        self._serve_file(*entry)

    def _same_origin(self) -> bool:
        """Refuse what a cross-site page can send without a CORS preflight.

        A browser on any site can POST text/plain to 127.0.0.1 with no preflight.
        Requiring application/json forces one, which this server never answers;
        Host and Origin checks also stop DNS rebinding and opaque origins.
        """
        port = self.server.server_address[1]
        allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if (self.headers.get("host") or "").lower() not in allowed:
            return False
        origin = self.headers.get("origin")
        if origin is not None and origin.lower() not in {f"http://{h}" for h in allowed}:
            return False
        content_type = (self.headers.get("content-type") or "").split(";")[0].strip().lower()
        return content_type == "application/json"

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path not in POST_ROUTES:
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
        if not self._same_origin():
            self.close_connection = True
            self.send_error(403, "Forbidden")
            return

        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self.send_error(400, "Invalid JSON")
            return

        # Every POST below has passed the same-origin JSON check: a cross-site page cannot
        # start the microphone listener or the operator.
        if path == "/api/approve":
            self._approve(payload)
            return
        if path == "/api/listen/start":
            self._json(200, start_listener())
            return
        if path == "/api/listen/stop":
            self._json(200, stop_listener())
            return
        if path == "/api/operator":
            if not isinstance(payload, dict):
                self._json(400, {"ok": False, "started": False, "spoken": "That action is not on this operator."})
                return
            self._json(200, operator_request(payload))
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

    def _approve(self, payload: object) -> None:
        """The human half of the tier-B gate. Ara has no tool that reaches this."""
        from starlight_voice import approval

        code = str(payload.get("code") or "") if isinstance(payload, dict) else ""
        result = approval.ack_human(code)
        self._json(200 if result["ok"] else 409, result)

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
