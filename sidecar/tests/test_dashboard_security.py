"""Exercise the handler with in-memory streams; never bind/start a server."""

import importlib.util
import io
import json
import threading
from email.message import Message
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location("voice_dashboard_security", Path(__file__).resolve().parents[2] / "dashboard/server.py")
dashboard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dashboard)


@pytest.mark.parametrize("changes", [
    {"Host": "attacker.example:8765"}, {"Origin": "https://attacker.example"}, {"Origin": "null"},
    {"Origin": "http://localhost:8765"}, {"Content-Type": "text/plain"},
    {"Sec-Fetch-Site": "cross-site"}, {"Transfer-Encoding": "chunked"}, {"Origin": None},
])
def test_same_origin_guard_rejects_rebinding_csrf_and_ambiguous_bodies(changes):
    headers = {"Host": "127.0.0.1:8765", "Origin": "http://127.0.0.1:8765", "Content-Type": "application/json"}
    for key, value in changes.items():
        if value is None:
            headers.pop(key, None)
        else:
            headers[key] = value
    assert not dashboard.trusted_voice_request(headers, 8765)


def test_exact_local_origin_is_accepted():
    assert dashboard.trusted_voice_request({"Host": "localhost:8765", "Origin": "http://localhost:8765", "Content-Type": "application/json"}, 8765)


def handler(body=b"{}", *, duplicate_length=False):
    value = object.__new__(dashboard.DashboardHandler)
    value.headers = Message()
    for key, content in {"Host": "127.0.0.1:8765", "Origin": "http://127.0.0.1:8765", "Content-Type": "application/json", "Content-Length": str(len(body))}.items():
        value.headers[key] = content
    if duplicate_length:
        value.headers["Content-Length"] = str(len(body))
    value.rfile, value.wfile = io.BytesIO(body), io.BytesIO()
    value.path = "/voice/session"
    value.connection = SimpleNamespace(settimeout=lambda _: None)
    value.server = SimpleNamespace(server_address=("127.0.0.1", 8765), voice_slots=threading.BoundedSemaphore(4))
    results = []
    value._json = lambda status, result: results.append((status, result))
    return value, results


@pytest.mark.parametrize("body,duplicate_length,status", [
    (b"{}", True, 413), (b"X" * 8193, False, 413), (b"[]", False, 400), (b"not JSON", False, 400),
])
def test_body_bounds_without_server_start(body, duplicate_length, status):
    value, results = handler(body, duplicate_length=duplicate_length)
    value._voice_post()
    assert results[0][0] == status
    assert value.server.voice_slots.acquire(blocking=False)


def test_broker_errors_return_only_redacted_message():
    from starlight_voice.native_bridge import BridgeError

    async def unavailable(payload):
        raise BridgeError("Provider unavailable", 502)

    value, results = handler(json.dumps({"provider": "grok", "voice": "ara"}).encode())
    value.server.voice_bridge = SimpleNamespace(start=unavailable)
    value._voice_post()
    assert results == [(502, {"error": "Provider unavailable"})]
