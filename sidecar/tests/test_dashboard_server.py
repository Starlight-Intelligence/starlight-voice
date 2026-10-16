"""Hardening tests for the dashboard HTTP server (DoS guard, healthz, write path)."""

import json
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

_DASH = Path(__file__).resolve().parents[2] / "dashboard"
sys.path.insert(0, str(_DASH))
import server as dash  # noqa: E402


@pytest.fixture
def live_server():
    srv = dash.make_server(0)  # ephemeral port
    host, port = srv.server_address
    base = f"http://{host}:{port}"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    # wait for readiness (kills the thread-startup race that made this flaky)
    for _ in range(50):
        try:
            urllib.request.urlopen(base + "/healthz", timeout=1)
            break
        except Exception:
            time.sleep(0.02)
    try:
        yield base
    finally:
        srv.shutdown()


def test_live_page_and_api(live_server, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("STARLIGHT_VOICE_STATE_DIR", str(tmp_path))
    from starlight_voice.session_store import publish

    publish("armed", voice="ara", memory="idle")
    page = urllib.request.urlopen(live_server + "/live", timeout=5)
    html = page.read()
    assert b"Live session" in html
    assert b'id="orbs"' in html
    assert b"getUserMedia" not in html
    body = json.loads(urllib.request.urlopen(live_server + "/api/live", timeout=5).read())
    assert body["phase"] == "armed"
    sessions = json.loads(urllib.request.urlopen(live_server + "/api/sessions", timeout=5).read())
    assert sessions["sessions"] == []


def test_healthz_ok(live_server) -> None:
    resp = urllib.request.urlopen(live_server + "/healthz", timeout=5)
    assert json.loads(resp.read())["ok"] is True
    assert resp.headers["X-Content-Type-Options"] == "nosniff"  # security header present


def test_oversized_post_is_rejected(live_server) -> None:
    # Assert the GUARANTEE (oversized refused), not the transport detail: rejecting a large
    # POST without draining the body can surface as a clean 413 OR a connection abort (the
    # server closes rather than read 64KB+). Both mean the memory-DoS guard fired.
    big = b'{"x":"' + b"a" * 70000 + b'"}'  # > 64KB MAX_BODY
    req = urllib.request.Request(live_server + "/ratings", data=big, method="POST")
    try:
        urllib.request.urlopen(req, timeout=5)
        raise AssertionError("oversized POST must be rejected")
    except urllib.error.HTTPError as exc:
        assert exc.code == 413
    except OSError:
        pass  # connection aborted == refused-without-reading; guard still fired


def test_valid_rating_writes_ok(live_server, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(dash, "RATINGS", tmp_path / "r.jsonl")  # don't touch real ratings
    data = json.dumps({"option": "hybrid", "rating": 9}).encode("utf-8")
    req = urllib.request.Request(live_server + "/ratings", data=data, method="POST")
    resp = urllib.request.urlopen(req, timeout=5)
    assert json.loads(resp.read())["ok"] is True
    assert (tmp_path / "r.jsonl").exists()


def test_status_endpoint_returns_live_state(live_server) -> None:
    resp = urllib.request.urlopen(live_server + "/status", timeout=5)
    data = json.loads(resp.read())
    assert "variants" in data and "settings" in data  # cockpit's data source
    assert {v["key"] for v in data["variants"]} == {"component", "openai-realtime", "gemini-live", "x-voice"}
    assert data["live"]["phase"] in {"stopped", "armed", "open", "speaking"}
    assert isinstance(data["sessions"], list)


def test_unknown_post_route_404(live_server) -> None:
    req = urllib.request.Request(live_server + "/nope", data=b"{}", method="POST")
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req, timeout=5)
    assert exc.value.code == 404


def test_root_serves_landing(live_server) -> None:
    resp = urllib.request.urlopen(live_server + "/", timeout=5)
    body = resp.read().decode("utf-8")
    assert resp.headers["content-type"].startswith("text/html")
    assert "Starlight Voice" in body and "Open voice" in body
    assert 'href="/voice/"' in body


def test_console_and_shared_tokens_served(live_server) -> None:
    console = urllib.request.urlopen(live_server + "/dashboard/cockpit.html", timeout=5)
    assert "Operator Console" in console.read().decode("utf-8")
    tokens = urllib.request.urlopen(live_server + "/tokens.css", timeout=5)
    assert tokens.headers["content-type"].startswith("text/css")
    assert "--voltage" in tokens.read().decode("utf-8")  # the single source of truth


def test_voice_experience_and_runtime_assets_served(live_server) -> None:
    voice = urllib.request.urlopen(live_server + "/voice/", timeout=5)
    body = voice.read().decode("utf-8")
    assert voice.headers["content-type"].startswith("text/html")
    assert "Starlight Voice" in body and "Start listening" in body
    assert "Content-Security-Policy" in body
    assert "fonts.googleapis.com" not in body

    for asset in ("/voice/voice.css", "/voice/voice.js", "/voice/voice-runtime.mjs"):
        response = urllib.request.urlopen(live_server + asset, timeout=5)
        assert response.status == 200


def test_voice_source_map_and_unlisted_assets_remain_fail_closed(live_server) -> None:
    for path in ("/voice/voice.js.map", "/voice/private.json"):
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(live_server + path, timeout=5)
        assert exc.value.code == 404


@pytest.mark.parametrize("bad", ["/server.py", "/.git/config", "/sidecar/pyproject.toml", "/../site/tokens.css"])
def test_disallowed_get_is_fail_closed_404(live_server, bad) -> None:
    # The allowlist must refuse anything not explicitly served — no repo source leaks.
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(live_server + bad, timeout=5)
    assert exc.value.code == 404
