"""Operator results. Imports the shipped functions. No process is spawned."""

import json
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import pytest

from starlight_voice.operator_actions import _github_login, hold_handoff, lookup_memory, open_named
from starlight_voice.x_voice import MODEL, readiness

_DASH = Path(__file__).resolve().parents[2] / "dashboard"
sys.path.insert(0, str(_DASH))
import server as dash  # noqa: E402


@pytest.fixture
def live_server():
    srv = dash.make_server(0)
    host, port = srv.server_address
    base = f"http://{host}:{port}"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
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


def _forbid_spawn(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args, **_kwargs):
        raise AssertionError("spawned")

    monkeypatch.setattr(subprocess, "Popen", boom)
    monkeypatch.setattr(subprocess, "run", boom)


def test_memory_lookup_names_the_query(monkeypatch, tmp_path) -> None:
    _forbid_spawn(monkeypatch)
    monkeypatch.setenv("STARLIGHT_VOICE_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("STARLIGHT_DESK_FILES", str(tmp_path / "absent.md"))
    found = lookup_memory("voice search")
    assert found["started"] is False
    assert found["ran"] is True
    assert found["target"] == "voice search"
    assert "voice search" in str(found["spoken"]).lower()


def test_open_names_tab_and_desktop(monkeypatch) -> None:
    launched: list[str] = []
    monkeypatch.setattr("starlight_voice.operator_actions._launch", lambda target: launched.append(target))
    _forbid_spawn(monkeypatch)
    tab = open_named("tab", "http://127.0.0.1:8765/live")
    desktop = open_named("desktop", "Starlight desk")
    desk = Path.home() / "Desktop"
    assert tab["started"] is False
    assert tab["ran"] is True
    assert tab["target"] == "http://127.0.0.1:8765/live"
    assert "http://127.0.0.1:8765/live" in str(tab["spoken"])
    assert desktop["started"] is False
    assert desktop["ran"] is True
    assert desktop["target"] == "Starlight desk"
    assert "Starlight desk" in str(desktop["spoken"])
    assert launched == ["http://127.0.0.1:8765/live", str(desk)]


def test_github_login_reads_the_account(monkeypatch) -> None:
    class Proc:
        returncode = 0
        stdout = ""
        stderr = "Logged in to github.com account frankxai (keyring)"

    def fake_run(argv, **kwargs):
        assert argv == ["gh", "auth", "status"]
        assert kwargs.get("shell") is False
        return Proc()

    monkeypatch.setattr(subprocess, "run", fake_run)
    ran, account = _github_login()
    assert ran is True
    assert account == "frankxai"


def test_handoff_names_grok_and_github_without_spawn(monkeypatch) -> None:
    monkeypatch.setattr("starlight_voice.operator_actions._github_login", lambda: (True, "frankxai"))
    _forbid_spawn(monkeypatch)
    for seat, label in (("grok-local", "Grok CLI"), ("github", "GitHub")):
        held = hold_handoff("public note for the seat", seat)
        assert held["started"] is False
        assert held["primary"] == seat
        assert held["target"] == label
        assert label in str(held["spoken"])
        assert "did not start" in str(held["spoken"]).lower()
        assert held["ran"] is True
        if seat == "github":
            assert held["account"] == "frankxai"
            assert "frankxai" in str(held["spoken"])


def test_expired_login_is_not_ready(monkeypatch) -> None:
    monkeypatch.setattr(
        "starlight_voice.x_voice.token_status",
        lambda: {"present": False, "source": "grok-cli-expired"},
    )
    monkeypatch.setattr(
        "starlight_voice.x_voice.open_spend_gate",
        lambda: {"ok": False, "why": "no-key", "spoken": ""},
    )
    monkeypatch.setattr("starlight_voice.x_voice._module_present", lambda _name: True)
    monkeypatch.setattr("starlight_voice.x_voice.clap_selfcheck", lambda: True)

    class _Script:
        def is_file(self) -> bool:
            return True

    monkeypatch.setattr("starlight_voice.x_voice.dispatch_script", lambda: _Script())
    report = readiness()
    assert report["ready"] is False
    assert report["token_source"] == "grok-cli-expired"
    assert report["model"] == MODEL == "grok-voice-latest"


def test_operator_route_names_each_target(live_server, monkeypatch, tmp_path) -> None:
    launched: list[str] = []
    monkeypatch.setattr("starlight_voice.operator_actions._launch", lambda target: launched.append(target))
    monkeypatch.setattr("starlight_voice.operator_actions._github_login", lambda: (True, "frankxai"))
    _forbid_spawn(monkeypatch)
    monkeypatch.setenv("STARLIGHT_VOICE_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("STARLIGHT_DESK_FILES", str(tmp_path / "absent.md"))
    cases = [
        {"action": "lookup", "query": "voice search"},
        {"action": "open", "kind": "tab", "target": "http://127.0.0.1:8765/live"},
        {"action": "open", "kind": "desktop", "target": "Starlight desk"},
        {"action": "handoff", "seat": "grok-local", "utterance": "public note for the seat"},
        {"action": "handoff", "seat": "github", "utterance": "public note for the seat"},
    ]
    named = ("voice search", "http://127.0.0.1:8765/live", "Starlight desk", "Grok CLI", "GitHub")
    for payload, needle in zip(cases, named, strict=True):
        request = urllib.request.Request(
            live_server + "/api/operator",
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={"content-type": "application/json"},
        )
        body = json.loads(urllib.request.urlopen(request, timeout=5).read())
        assert body["started"] is False
        assert body["ran"] is True
        assert needle in str(body.get("spoken") or "")
        assert needle == body.get("target") or needle in str(body.get("target") or "")
    assert launched == ["http://127.0.0.1:8765/live", str(Path.home() / "Desktop")]
