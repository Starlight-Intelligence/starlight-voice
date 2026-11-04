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
    assert b"Ara" in html
    assert b'id="queen"' in html
    assert b'id="phase"' in html
    for house in (b"FrankX", b"Arcanea", b"GenCreator", b"Starlight", b"Income"):
        assert house in html
    assert b'href="https://animations.dev"' in html
    assert b'href="https://vercel.com/design/guidelines"' in html
    assert b'href="https://docs.livekit.io/reference/components/agents-ui/"' in html
    assert b'src="https://animations.dev' not in html
    assert b'src="https://vercel.com' not in html
    assert b'src="https://docs.livekit.io' not in html
    assert b'id="orbs"' in html
    assert b"Start Ara" in html
    assert b"Stop Ara" in html
    assert b"clap twice" in html
    assert b"Look up" in html
    assert b"Open tab" in html
    assert b"Open desktop" in html
    assert b"Grok CLI" in html
    assert b"GitHub" in html
    assert b'id="result"' in html
    assert b"/api/listen/start" in html
    assert b"/api/listen/stop" in html
    assert b"/api/operator" in html
    assert b"getUserMedia" not in html
    body = json.loads(urllib.request.urlopen(live_server + "/api/live", timeout=5).read())
    assert body["phase"] == "armed"
    sessions = json.loads(urllib.request.urlopen(live_server + "/api/sessions", timeout=5).read())
    assert sessions["sessions"] == []


def test_stop_listener_marks_stopped(live_server, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("STARLIGHT_VOICE_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("STARLIGHT_VOICE_LISTEN_DRY", "1")
    monkeypatch.setenv("STARLIGHT_VOICE_LISTEN_PID", str(tmp_path / "listen.pid"))
    (tmp_path / "listen.pid").write_text("4300", encoding="utf-8")
    from starlight_voice.session_store import publish, read_live

    publish("armed", voice="ara", memory="idle")
    body = json.loads(_post(live_server, "/api/listen/stop", {}).read())
    assert body["ok"] is True
    assert body["phase"] == "stopped"
    assert body["stopped"] == []
    assert read_live()["phase"] == "stopped"
    assert not (tmp_path / "listen.pid").exists()


def test_start_listener_uses_the_shipped_command(live_server, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("STARLIGHT_VOICE_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("STARLIGHT_VOICE_LISTEN_DRY", "1")
    monkeypatch.setenv("STARLIGHT_VOICE_LISTEN_LOG", str(tmp_path / "listen.out.log"))
    seen: dict[str, object] = {}

    def fake_popen(argv, **kwargs):
        seen["argv"] = list(argv)
        seen["cwd"] = kwargs.get("cwd")
        from starlight_voice.session_store import publish

        publish("armed", voice="ara", memory="idle")

        class Proc:
            pid = 1

        return Proc()

    monkeypatch.setattr(dash.subprocess, "Popen", fake_popen)
    # CI and fresh worktrees have no sidecar/.venv; the route only needs the runtime to exist.
    runtime = tmp_path / "python.exe"
    runtime.write_bytes(b"")
    monkeypatch.setattr(dash, "_listener_python", lambda: runtime)
    body = json.loads(_post(live_server, "/api/listen/start", {}).read())
    argv = seen["argv"]
    assert isinstance(argv, list)
    assert body["ok"] is True
    assert body["phase"] == "armed"
    assert argv[1:] == ["-m", "starlight_voice", "listen"]
    assert str(argv[0]).endswith("python.exe")
    assert seen["cwd"] == str(dash.ROOT)


def test_desk_counts_without_card_text(live_server, tmp_path, monkeypatch) -> None:
    pulse = tmp_path / "chair-pulse.json"
    pulse.write_text(
        json.dumps(
            {
                "ts": "2026-10-17T20:31:57.864Z",
                "machine": {"loopStatus": "idle-held", "freeRamMB": 1},
                "inbox": [],
                "heldTonight": [{"id": "secret-card", "do": "do not show this prompt"}],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(dash, "CHAIR", pulse)
    body = json.loads(urllib.request.urlopen(live_server + "/api/desk", timeout=5).read())
    assert body["loop"] == "idle-held"
    assert body["started"] is False
    assert body["held"] == 1
    assert body["inbox"] == 0
    assert "secret-card" not in json.dumps(body)
    assert "freeRamMB" not in json.dumps(body)


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
    req = urllib.request.Request(
        live_server + "/ratings", data=data, method="POST", headers={"content-type": "application/json"}
    )
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


def _post(base: str, path: str, body: dict, headers: dict[str, str] | None = None):
    req = urllib.request.Request(
        base + path,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={"content-type": "application/json", **(headers or {})},
    )
    return urllib.request.urlopen(req, timeout=5)


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "https://evil.example"},
        {"Origin": "null"},
        {"content-type": "text/plain"},
        {"Host": "attacker.example:8765"},
    ],
)
@pytest.mark.parametrize("path", ["/api/approve", "/api/listen/start", "/api/listen/stop", "/api/operator"])
def test_cross_site_post_is_refused(live_server, headers, path) -> None:
    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(live_server, path, {}, headers)
    assert exc.value.code == 403


def test_bare_post_cannot_start_the_microphone(live_server) -> None:
    """A form or no-cors fetch from any site sends no JSON content-type; it must not arm the mic."""
    request = urllib.request.Request(live_server + "/api/listen/start", data=b"x", method="POST")
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(request, timeout=5)
    assert exc.value.code == 403


def test_same_origin_approve_acks_the_waiting_proposal(live_server, monkeypatch, tmp_path) -> None:
    from starlight_voice import approval

    repos = tmp_path / "repos"
    (repos / "frankx-site" / ".git").mkdir(parents=True)
    monkeypatch.setenv("STARLIGHT_REPOS_ROOT", str(repos))
    monkeypatch.setenv("STARLIGHT_QUEEN_DIR", str(tmp_path / "queen"))
    monkeypatch.setenv("STARLIGHT_VOICE_PENDING_DIR", str(tmp_path / "pending"))
    allow = tmp_path / "repos.allow"
    allow.write_text("frankx-site\n", encoding="utf-8")
    monkeypatch.setenv("STARLIGHT_VOICE_REPO_ALLOWLIST", str(allow))
    packet = {"ok": True, "job": "implement", "primary": "codex-cloud", "refuse": False,
              "data_class": "public", "utterance": "public tidy frankx site"}
    proposal = approval.propose(packet)["proposal"]
    approval.confirm_spoken(proposal)
    pending = json.loads(urllib.request.urlopen(live_server + "/api/pending", timeout=5).read())["pending"]
    assert pending[0]["request"] == "public tidy frankx site"
    resp = _post(live_server, "/api/approve", {"code": pending[0]["code"]}, {"Origin": live_server})
    assert json.loads(resp.read())["queued"] is True
    assert len(list((tmp_path / "queen" / "inbox").glob("*.json"))) == 1


def test_approve_without_a_code_is_a_conflict(live_server, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("STARLIGHT_VOICE_PENDING_DIR", str(tmp_path / "pending"))
    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(live_server, "/api/approve", {})
    assert exc.value.code == 409
