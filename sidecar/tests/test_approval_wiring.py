from __future__ import annotations

import json
from pathlib import Path

import pytest

from starlight_voice import __main__ as cli
from starlight_voice import approval
from starlight_voice.x_voice import handle_meta_harness, server_tool_hit


@pytest.fixture()
def estate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    repos = tmp_path / "repos"
    (repos / "frankx-site" / ".git").mkdir(parents=True)
    queen = tmp_path / "queen"
    (queen / "inbox").mkdir(parents=True)
    allow = tmp_path / "repos.allow"
    allow.write_text("frankx-site\n", encoding="utf-8")
    monkeypatch.setenv("STARLIGHT_REPOS_ROOT", str(repos))
    monkeypatch.setenv("STARLIGHT_QUEEN_DIR", str(queen))
    monkeypatch.setenv("STARLIGHT_VOICE_PENDING_DIR", str(tmp_path / "pending"))
    monkeypatch.setenv("STARLIGHT_VOICE_REPO_ALLOWLIST", str(allow))
    return queen


def _routed(**overrides: object):
    packet = {
        "ok": True,
        "started": False,
        "job": "implement",
        "primary": "claude-local",
        "refuse": False,
        "data_class": "public",
        "utterance": "public fix the footer in frankx site",
        "packet": True,
        "spoken": "implement goes to claude-local. Held. I did not start it.",
    }
    packet.update(overrides)
    return lambda **_: packet


def test_public_repo_work_returns_a_proposal_not_a_start(estate: Path, monkeypatch) -> None:
    monkeypatch.setattr("starlight_voice.x_voice.route_work", _routed())
    out = handle_meta_harness({"utterance": "public fix the footer in frankx site", "job": "implement"})
    assert out["started"] is False
    assert out["queued"] is False
    assert approval._ID.fullmatch(out["proposal"])
    assert out["proposal"][-4:] in out["spoken"]
    assert list((estate / "inbox").glob("*.json")) == []


def test_tier_c_packet_passes_through_without_proposal(estate: Path, monkeypatch) -> None:
    monkeypatch.setattr("starlight_voice.x_voice.route_work", _routed(job="integrate"))
    out = handle_meta_harness({"utterance": "merge it", "job": "merge"})
    assert "proposal" not in out


def test_cli_approve_without_code_only_lists(estate: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr("starlight_voice.x_voice.route_work", _routed())
    proposal = handle_meta_harness({"utterance": "x", "job": "implement"})["proposal"]
    approval.confirm_spoken(proposal)
    assert cli.main(["approve"]) == 0
    listed = json.loads(capsys.readouterr().out)["pending"]
    assert listed[0]["request"] == "public fix the footer in frankx site"
    assert list((estate / "inbox").glob("*.json")) == []


def test_cli_approve_with_code_queues(estate: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr("starlight_voice.x_voice.route_work", _routed())
    proposal = handle_meta_harness({"utterance": "x", "job": "implement"})["proposal"]
    approval.confirm_spoken(proposal)
    assert cli.main(["approve", proposal[-4:]]) == 0
    assert json.loads(capsys.readouterr().out)["queued"] is True
    assert len(list((estate / "inbox").glob("*.json"))) == 1


def test_new_tools_are_client_tools() -> None:
    for name in ("confirm_handoff", "handoff_status"):
        assert server_tool_hit({"type": "response.function_call_arguments.done", "name": name}) == ""
