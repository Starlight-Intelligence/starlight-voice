import json
import subprocess

import pytest

from starlight_voice import __main__ as cli
from starlight_voice.cognition.dispatch import Dispatcher, approval_tier, build_handoff_packet
from starlight_voice.cognition.fleet import complexity_score, select_agent
from starlight_voice.cognition.ledger import read_runs, record_run
from starlight_voice.cognition.router import RouteTier
from starlight_voice.pipeline import AgentPipeline


@pytest.fixture(autouse=True)
def _isolate_preview_context(tmp_path, monkeypatch):
    monkeypatch.setenv("STARLIGHT_BRIEF_REPOS", str(tmp_path))
    monkeypatch.setattr("starlight_voice.cognition.sessions.resolve_repo", lambda task: None)


@pytest.fixture
def spawn_attempts(monkeypatch):
    attempts = []

    def legacy_spawn(packet):
        attempts.append(packet.task)
        return True

    def forbidden_process(*args, **kwargs):
        pytest.fail("Packet previews must never launch a subprocess")

    # Trap the former spawn seam without opening logs or resolving real harnesses.
    monkeypatch.setattr(Dispatcher, "_spawn", staticmethod(legacy_spawn), raising=False)
    monkeypatch.setattr(subprocess, "Popen", forbidden_process)
    return attempts


def test_complexity_routes_trivial_to_opencode() -> None:
    agent = select_agent(complexity_score("fix a typo in the readme"))
    assert agent.cli == "opencode"


def test_complexity_routes_high_to_fable5_seat() -> None:
    agent = select_agent(complexity_score("refactor the auth module across the repo and fix the test suite"))
    assert agent.model == "claude-fable-5"  # 7-8 seat = Fable 5 (Terminal-Bench leader)


def test_substrate_routes_to_deepagent() -> None:
    agent = select_agent(complexity_score("a breaking change to the SIP protocol schema migration"))
    assert agent.cli == "dcode"


def test_approval_tiers_default_deny() -> None:
    assert approval_tier("read the config and summarize") == "A"   # provably read-only
    assert approval_tier("force push to main") == "D"              # ALWAYS-ASK hard-block
    assert approval_tier("rotate the API key") == "D"              # ALWAYS-ASK hard-block
    assert approval_tier("a breaking change to the SIP protocol") == "C"


def test_adversarial_destructive_never_tier_a() -> None:
    # the exact fail-OPEN cases the review found — must NOT auto-run
    assert approval_tier("nuke the prod database") == "D"
    assert approval_tier("wipe production") == "D"
    assert approval_tier("overwrite main with my branch") == "B"   # mutating -> ack, not A
    assert approval_tier("delete the old logs") == "B"


def test_benign_task_gets_ack_not_false_block() -> None:
    # 'merge sort' previously false-blocked on substring 'merge'; now it's a normal ack (B), not D
    assert approval_tier("write a merge sort implementation") == "B"


def test_packet_has_routing_and_tier() -> None:
    pkt = build_handoff_packet("delete the stale branches").to_dict()
    assert pkt["packet_version"] == 1
    assert pkt["approval"]["tier"] == "B"          # mutating -> ack required
    assert pkt["approval"]["required"] is True
    assert pkt["target"]["cli"]                      # an agent was chosen
    assert pkt["packet_id"]                          # ulid present


def test_dispatch_dry_run_does_not_spawn() -> None:
    out = Dispatcher(live=False).dispatch("read the project layout")
    assert out["status"] == "dry-run"
    assert "packet" in out


def test_dispatch_live_does_not_claim_pending_approval() -> None:
    out = Dispatcher(live=True).dispatch("publish the release and deploy")
    assert out["status"] == "unavailable"  # no admission or approval queue exists here


def test_dispatch_hard_blocks_always_ask_even_live() -> None:
    out = Dispatcher(live=True).dispatch("rotate the production API key")
    assert out["status"] == "hard-blocked"        # Tier D never runs, even live
    assert out["packet"]["approval"]["tier"] == "D"


def test_live_control_character_payload_is_unavailable() -> None:
    from starlight_voice.cognition.dispatch import build_handoff_packet

    # control-char payload (injection-style) is refused, never spawned
    pkt = build_handoff_packet("read the docs")
    object.__setattr__(pkt, "task", "ok\n; rm -rf /")
    out = Dispatcher(live=True).dispatch(pkt.task)
    assert out["status"] == "unavailable"


@pytest.mark.parametrize("task", [
    "read the project layout",
    "publish the release and deploy",
    "a breaking change to the SIP protocol",
    "stop",
    "read the docs\n; arbitrary instruction",
])
def test_live_requests_are_unavailable_without_controller_admission(task, spawn_attempts) -> None:
    out = Dispatcher(live=True).dispatch(task)
    assert out["status"] == "unavailable"
    assert "managed dispatch" in out["note"].lower()
    assert spawn_attempts == []
    assert read_runs()[-1]["status"] == "unavailable"
    assert read_runs()[-1]["pid"] is None


def test_read_only_hint_does_not_grant_execution_authority() -> None:
    packet = build_handoff_packet("read the docs").to_dict()
    assert packet["approval"]["tier"] == "A"
    assert packet["approval"]["required"] is True
    assert packet["execution"] == {"mode": "preview", "admitted": False}


def test_text_pipeline_announces_preview_without_started_work(spawn_attempts) -> None:
    response = AgentPipeline._response_for(RouteTier.CLI_AGENT, "read the project layout")
    assert response["dispatch"]["status"] == "dry-run"
    assert "preview" in response["text"].lower()
    assert "no harness has started" in response["text"].lower()
    assert spawn_attempts == []


@pytest.mark.parametrize("live,task,code,status", [
    (True, "read the project layout", 2, "unavailable"),
    (True, "rotate the production API key", 2, "hard-blocked"),
    (False, "read the project layout", 0, "dry-run"),
])
def test_cli_reports_dispatch_outcome(live, task, code, status, monkeypatch, capsys, spawn_attempts) -> None:
    monkeypatch.setattr(cli, "load_local_env", lambda: None)
    argv = ["dispatch", *(["--live"] if live else []), task]
    assert cli.main(argv) == code
    outcome = json.loads(capsys.readouterr().out)
    assert outcome["status"] == status
    assert spawn_attempts == []


def test_retry_preserves_prior_audit_without_creating_work(spawn_attempts) -> None:
    record_run({"packet_id": "historical", "task": "old run"}, "spawned", pid=999)
    first = Dispatcher(live=True).dispatch("read the docs")
    second = Dispatcher(live=True).dispatch("read the docs")
    assert first["packet"]["packet_id"] != second["packet"]["packet_id"]
    records = read_runs()
    assert [record["status"] for record in records] == ["spawned", "unavailable", "unavailable"]
    assert records[0]["pid"] == 999
    assert spawn_attempts == []
