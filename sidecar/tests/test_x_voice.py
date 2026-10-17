import json
import os
import subprocess
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest

from starlight_voice.meta_harness import (
    ground_class,
    ground_dirty,
    normalize_class,
    route_work,
)
from starlight_voice.spend import (
    daily_cap_seconds,
    open_budget,
    reserve,
    server_spend_gate,
    settle,
    used_seconds,
)
from starlight_voice.x_voice import (
    audio_delta,
    function_output,
    handle_meta_harness,
    key_from_infisical,
    note_transcript,
    parse_arguments,
    open_spend_gate,
    readiness,
    resolve_token,
    server_tool_hit,
    session_update,
    token_status,
)


@pytest.fixture(autouse=True)
def _hide_live_grok_login(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("STARLIGHT_GROK_AUTH", str(tmp_path / "missing-auth.json"))


def _decision(primary: str = "claude-local", spawn: bool = True) -> str:
    return json.dumps({
        "job": "judgment",
        "refuse": False,
        "primary": primary,
        "spawn": spawn,
        "command": "claude",
        "reasons": [],
        "next": "write GitHub packet",
    })


def test_session_update_pins_grok_voice_and_the_harness_tool(monkeypatch) -> None:
    monkeypatch.delenv("STARLIGHT_XVOICE_VOICE", raising=False)
    monkeypatch.delenv("STARLIGHT_XVOICE_REASONING", raising=False)
    message = session_update()
    session = message["session"]
    assert session["voice"] == "ara"
    assert session["turn_detection"]["type"] == "server_vad"
    assert session["reasoning"]["effort"] == "high"
    assert session["audio"]["input"]["format"]["rate"] == 24000
    assert session["audio"]["output"]["format"]["rate"] == 24000
    names = [tool.get("name") or tool.get("type") for tool in session["tools"]]
    assert names == ["web_search", "x_search", "meta_harness", "estate_lookup"]
    assert "started is always false" in session["instructions"]
    assert "no approval gate yet" in session["instructions"]
    assert "web_search" in session["instructions"]
    meta = next(tool for tool in session["tools"] if tool.get("name") == "meta_harness")
    assert "data_class" not in meta["parameters"]["properties"]
    assert "dirty" not in meta["parameters"]["properties"]


def test_grok_account_is_first(monkeypatch, tmp_path: Path) -> None:
    auth = tmp_path / "auth.json"
    auth.write_text(json.dumps({"acct": {"key": "cli-token-not-real", "expires_at": "2099-01-01T00:00:00+00:00"}}), encoding="utf-8")
    monkeypatch.setenv("STARLIGHT_GROK_AUTH", str(auth))
    monkeypatch.setenv("XAI_API_KEY", "xai-test-not-real")
    monkeypatch.setenv("STARLIGHT_XVOICE_INFISICAL", "0")
    assert resolve_token() == "cli-token-not-real"
    assert token_status()["source"] == "grok-cli"


def test_expired_grok_account_falls_through_to_an_xai_key(monkeypatch, tmp_path: Path) -> None:
    auth = tmp_path / "auth.json"
    auth.write_text(json.dumps({"acct": {"key": "cli-token-not-real", "expires_at": "2000-01-01T00:00:00+00:00"}}), encoding="utf-8")
    monkeypatch.setenv("STARLIGHT_GROK_AUTH", str(auth))
    monkeypatch.setenv("XAI_API_KEY", "xai-test-not-real")
    monkeypatch.setenv("STARLIGHT_XVOICE_INFISICAL", "0")
    assert resolve_token() == "xai-test-not-real"
    assert token_status()["source"] == "api-key"


def test_expired_grok_account_alone_is_not_a_token(monkeypatch, tmp_path: Path) -> None:
    auth = tmp_path / "auth.json"
    auth.write_text(json.dumps({"acct": {"key": "cli-token-not-real", "expires_at": "2000-01-01T00:00:00+00:00"}}), encoding="utf-8")
    monkeypatch.setenv("STARLIGHT_GROK_AUTH", str(auth))
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.setenv("STARLIGHT_XVOICE_INFISICAL", "0")
    assert resolve_token() is None
    assert token_status()["source"] == "grok-cli-expired"


def test_grok_account_skips_the_spending_limit(monkeypatch, tmp_path: Path) -> None:
    auth = tmp_path / "auth.json"
    auth.write_text(json.dumps({"acct": {"key": "cli-token-not-real", "expires_at": "2099-01-01T00:00:00+00:00"}}), encoding="utf-8")
    monkeypatch.setenv("STARLIGHT_GROK_AUTH", str(auth))
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    monkeypatch.delenv("XAI_MANAGEMENT_KEY", raising=False)
    monkeypatch.delenv("XAI_TEAM_ID", raising=False)
    monkeypatch.setenv("STARLIGHT_XVOICE_INFISICAL", "0")

    def boom(get_json=None):
        raise AssertionError("spend gate")

    monkeypatch.setattr("starlight_voice.x_voice.server_spend_gate", boom)
    report = readiness()
    assert report["token_source"] == "grok-cli"
    assert report["spend_gate"] == "grok-account"
    assert open_spend_gate()["why"] == "grok-account"
    assert "xai-spend-limit" not in report["missing"]
    assert "xai-key" not in report["missing"]


def test_an_xai_key_still_checks_the_spending_limit(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("STARLIGHT_GROK_AUTH", str(tmp_path / "missing-auth.json"))
    monkeypatch.setenv("XAI_API_KEY", "xai-test-not-real")
    monkeypatch.setenv("STARLIGHT_XVOICE_INFISICAL", "0")
    monkeypatch.delenv("XAI_MANAGEMENT_KEY", raising=False)
    monkeypatch.delenv("XAI_TEAM_ID", raising=False)

    def seen(get_json=None):
        return {"ok": False, "why": "xai-limit-unconfigured", "spoken": "shut"}

    monkeypatch.setattr("starlight_voice.x_voice.server_spend_gate", seen)
    gate = open_spend_gate()
    assert gate["ok"] is False
    assert gate["why"] == "xai-limit-unconfigured"


def test_non_xai_env_key_is_ignored(monkeypatch) -> None:
    monkeypatch.setenv("XAI_API_KEY", "openrouter-key-not-for-this-socket")
    assert resolve_token() is None


def test_infisical_rejects_a_non_xai_secret(monkeypatch) -> None:
    monkeypatch.setenv("STARLIGHT_XVOICE_INFISICAL", "1")
    assert key_from_infisical(lambda: "sk-or-not-for-this-socket") is None
    assert key_from_infisical(lambda: "xai-test-from-infisical") == "xai-test-from-infisical"


def test_infisical_is_second_when_the_env_key_is_not_xai_shaped(monkeypatch) -> None:
    monkeypatch.setenv("XAI_API_KEY", "openrouter-key-not-for-this-socket")
    monkeypatch.setattr("starlight_voice.x_voice.key_from_infisical", lambda: "xai-test-from-infisical")
    assert resolve_token() == "xai-test-from-infisical"
    monkeypatch.setenv("XAI_API_KEY", "xai-env-not-real")
    assert resolve_token() == "xai-env-not-real"


def test_search_cap_counts_a_finished_server_tool() -> None:
    assert server_tool_hit({"type": "response.web_search_call.completed"}) == "web_search"
    assert server_tool_hit({"type": "response.function_call_arguments.done", "name": "meta_harness"}) == ""
    assert server_tool_hit({"type": "response.x_search_call.in_progress"}) == ""


def test_session_can_omit_x_search() -> None:
    names = [tool.get("name") or tool.get("type") for tool in session_update(x_search=False)["session"]["tools"]]
    assert names == ["web_search", "meta_harness", "estate_lookup"]
    assert "x_search is off" in session_update(x_search=False)["session"]["instructions"]


def test_daily_cap_refuses_another_open(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("STARLIGHT_VOICE_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("STARLIGHT_XVOICE_DAILY_MINUTES", "15")
    held = reserve(15 * 60)
    assert held["ok"] is True
    budget = open_budget()
    assert budget["ok"] is False
    assert "stays closed" in str(budget["spoken"])
    monkeypatch.setenv("STARLIGHT_XVOICE_DAILY_MINUTES", "10000")
    assert daily_cap_seconds() == 60 * 60


def test_reserve_survives_a_new_process(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("STARLIGHT_VOICE_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("STARLIGHT_XVOICE_DAILY_MINUTES", "15")
    held = reserve(90)
    assert held["ok"] is True
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    env["STARLIGHT_VOICE_STATE_DIR"] = str(tmp_path)
    env["STARLIGHT_XVOICE_DAILY_MINUTES"] = "15"
    proc = subprocess.run(
        [sys.executable, "-c", "from starlight_voice.spend import used_seconds; print(used_seconds())"],
        cwd=Path(__file__).resolve().parents[2],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert float(proc.stdout.strip()) == pytest.approx(90)


def test_reserve_rolls_over_at_utc_midnight(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("STARLIGHT_VOICE_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("STARLIGHT_XVOICE_DAILY_MINUTES", "15")
    yesterday = datetime(2026, 10, 16, 23, 30, tzinfo=UTC)
    today = datetime(2026, 10, 17, 0, 5, tzinfo=UTC)
    assert reserve(800, now=yesterday)["ok"] is True
    budget = open_budget(now=today)
    assert budget["ok"] is True
    assert budget["max_s"] == 600
    assert used_seconds(now=today) == 0


def test_crash_keeps_the_reserve_until_settle(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("STARLIGHT_VOICE_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("STARLIGHT_XVOICE_DAILY_MINUTES", "15")
    held = reserve(800)
    assert held["ok"] is True
    assert used_seconds() == pytest.approx(800)
    assert reserve(200)["ok"] is False
    assert settle(str(held["id"]), 12) != "unreadable"
    assert used_seconds() == pytest.approx(12)
    assert reserve(200)["ok"] is True


def test_corrupt_ledger_stays_closed(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("STARLIGHT_VOICE_STATE_DIR", str(tmp_path))
    path = tmp_path / "spend.jsonl"
    path.write_text('{"kind":"reserve","id":"x","day":"2026-10-17","seconds":1}\nnot-json\n', encoding="utf-8")
    budget = open_budget()
    assert budget["ok"] is False
    assert "unreadable" in str(budget["spoken"])
    assert reserve(10)["ok"] is False
    assert used_seconds() is None


def test_second_listener_cannot_double_reserve(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("STARLIGHT_VOICE_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("STARLIGHT_XVOICE_DAILY_MINUTES", "15")
    barrier = threading.Barrier(2)
    results: list[dict] = []
    lock = threading.Lock()

    def go() -> None:
        barrier.wait()
        outcome = reserve(600)
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=go) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
    assert [thread.is_alive() for thread in threads] == [False, False]
    assert sorted(item["ok"] for item in results) == [False, True]
    assert used_seconds() == pytest.approx(600)


def test_server_limit_refuses_when_unread_or_spent(monkeypatch) -> None:
    assert server_spend_gate()["ok"] is False
    monkeypatch.setenv("XAI_MANAGEMENT_KEY", "mgmt-test-not-real")
    monkeypatch.setenv("XAI_TEAM_ID", "team-test")

    def broken(_path: str) -> dict:
        raise RuntimeError("status=500")

    assert server_spend_gate(broken)["why"] == "xai-limit-unread"

    def missing(path: str) -> dict:
        assert "mgmt-test-not-real" not in path
        assert path.startswith("/v1/billing/teams/team-test/")
        if path.endswith("/spending-limits"):
            return {"spendingLimits": {}}
        return {"coreInvoice": {"amountBeforeVat": "0"}}

    assert server_spend_gate(missing)["why"] == "xai-limit-missing"

    def spent(path: str) -> dict:
        if path.endswith("/spending-limits"):
            return {"spendingLimits": {"effectiveSl": {"val": "20000"}}}
        return {"coreInvoice": {"amountBeforeVatLimited": {"val": "20000"}}}

    assert server_spend_gate(spent)["why"] == "xai-limit-reached"

    def open_room(path: str) -> dict:
        if path.endswith("/spending-limits"):
            return {"spendingLimits": {"effectiveSl": {"val": "20000"}}}
        return {"coreInvoice": {"amountBeforeVat": "10"}}

    assert server_spend_gate(open_room)["ok"] is True

    def prepaid(path: str) -> dict:
        if path.endswith("/spending-limits"):
            return {"spendingLimits": {"effectiveSl": {"val": "0"}}}
        if path.endswith("/invoice/preview"):
            return {"coreInvoice": {"amountBeforeVat": "0"}}
        assert path.endswith("/prepaid/balance")
        return {"total": {"val": "-1000"}}

    assert server_spend_gate(prepaid)["why"] == "xai-prepaid"

    def prepaid_empty(path: str) -> dict:
        if path.endswith("/spending-limits"):
            return {"spendingLimits": {"effectiveSl": {"val": "0"}}}
        if path.endswith("/prepaid/balance"):
            return {"total": {"val": "0"}}
        return {"coreInvoice": {}}

    assert server_spend_gate(prepaid_empty)["ok"] is False


def test_management_get_uses_the_documented_host(monkeypatch) -> None:
    seen: dict[str, str] = {}

    class _Body:
        def __init__(self, payload: bytes) -> None:
            self._payload = payload

        def read(self, _size: int) -> bytes:
            return self._payload

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> bool:
            return False

    pages = iter([
        b'{"spendingLimits":{"effectiveSl":{"val":"20000"}}}',
        b'{"coreInvoice":{"amountBeforeVatLimited":{"val":"1"}}}',
    ])

    def urlopen(request, timeout=0):
        seen["url"] = request.full_url
        header = request.get_header("Authorization")
        assert header is not None
        assert "mgmt-test-not-real" not in request.full_url
        assert timeout == 10
        return _Body(next(pages))

    monkeypatch.setenv("XAI_MANAGEMENT_KEY", "mgmt-test-not-real")
    monkeypatch.setenv("XAI_TEAM_ID", "team test")
    monkeypatch.setattr("starlight_voice.spend.urlopen", urlopen)
    gate = server_spend_gate()
    assert gate["ok"] is True
    assert seen["url"] == "https://management-api.x.ai/v1/billing/teams/team%20test/postpaid/invoice/preview"


def test_audio_and_transcript_extractors() -> None:
    assert audio_delta({"type": "response.output_audio.delta", "delta": "abc"}) == "abc"
    assert audio_delta({"type": "response.audio.delta", "audio": "xyz"}) == "xyz"
    assert audio_delta({"type": "response.done"}) is None
    assert note_transcript({"type": "response.output_audio_transcript.done", "transcript": " Held. "}) == "starlight: Held."
    assert note_transcript({"type": "response.done", "transcript": "no"}) is None


def test_function_output_carries_the_hold() -> None:
    item = function_output("call_1", {"started": False, "primary": "grok-local"})
    assert item["item"]["call_id"] == "call_1"
    assert item["item"]["type"] == "function_call_output"
    assert "grok-local" in item["item"]["output"]


def test_parse_arguments_rejects_a_list() -> None:
    assert parse_arguments('{"job": "review"}')["job"] == "review"
    with pytest.raises(ValueError):
        parse_arguments("[1]")


def test_route_work_holds_a_public_seat() -> None:
    def runner(argv: list[str]) -> str:
        assert argv[0] == "node"
        assert "dispatch-route.mjs" in argv[1]
        assert "--job" in argv
        assert argv[argv.index("--job") + 1] == "judgment"
        assert "--class" not in argv
        assert "claude" not in argv
        return _decision()

    out = route_work(
        utterance="review the public voice operator",
        job="review",
        runner=runner,
        dirty_probe=lambda: False,
    )
    assert out["started"] is False
    assert out["packet"] is True
    assert out["data_class"] == "public"
    assert out["primary"] == "claude-local"
    assert out["table_would_spawn"] is True
    assert "command" not in out
    assert "did not start" in str(out["spoken"])


def test_unknown_class_refuses_after_the_packet() -> None:
    seen: list[str] = []

    def runner(argv: list[str]) -> str:
        seen.extend(argv)
        return _decision()

    out = route_work(utterance="review the voice operator", job="review", runner=runner, dirty_probe=lambda: False)
    assert seen
    assert "--class" not in seen
    assert out["packet"] is True
    assert out["started"] is False
    assert out["refuse"] is True
    assert out["data_class"] == "unknown"
    assert out["primary"] == ""
    assert "unknown-data-class" in out["reasons"]
    assert "command" not in out


def test_unknown_claim_does_not_become_public() -> None:
    assert normalize_class("nope") is None
    assert normalize_class("") is None
    assert normalize_class(None) is None
    assert ground_class("review the voice operator") is None
    out = route_work(
        utterance="review the public page",
        job="review",
        claimed_class="nope",
        runner=lambda _argv: _decision(),
        dirty_probe=lambda: False,
    )
    assert out["data_class"] == "unknown"
    assert out["refuse"] is True
    assert out["started"] is False


def test_model_claim_cannot_raise_a_public_ground() -> None:
    seen: list[str] = []

    def runner(argv: list[str]) -> str:
        seen.extend(argv)
        return _decision()

    out = route_work(
        utterance="review the public page",
        job="review",
        claimed_class="secret",
        runner=runner,
        dirty_probe=lambda: False,
    )
    assert seen
    assert "--class" not in seen
    assert out["data_class"] == "unknown"
    assert out["refuse"] is True
    assert out["started"] is False
    assert out["packet"] is True
    assert out["primary"] == ""
    assert "command" not in out


def test_restricted_marker_beats_a_public_claim() -> None:
    def runner(argv: list[str]) -> str:
        assert argv[argv.index("--class") + 1] == "secret"
        return _decision()

    out = route_work(
        utterance="rotate the api key on the public page",
        job="review",
        claimed_class="public",
        runner=runner,
        dirty_probe=lambda: False,
    )
    assert out["data_class"] == "secret"
    assert out["started"] is False
    assert out["packet"] is True


def test_two_classes_in_one_utterance_are_unknown() -> None:
    out = route_work(
        utterance="put the api key on the invoice",
        job="implement",
        runner=lambda _argv: _decision(primary="codex-local"),
        dirty_probe=lambda: False,
    )
    assert out["data_class"] == "unknown"
    assert out["refuse"] is True
    assert out["started"] is False


def test_model_dirty_flag_is_not_forwarded(monkeypatch) -> None:
    captured: dict = {}

    def fake_route(**kwargs):
        captured.update(kwargs)
        return {"ok": False, "started": False, "spoken": "held"}

    monkeypatch.setattr("starlight_voice.x_voice.route_work", fake_route)
    out = handle_meta_harness({
        "utterance": "review the public page",
        "job": "review",
        "data_class": "public",
        "dirty": False,
    })
    assert out["started"] is False
    assert captured["claimed_class"] == "public"
    assert "dirty" not in captured


def test_dirty_comes_from_git(tmp_path: Path) -> None:
    clean = tmp_path / "clean"
    dirty = tmp_path / "dirty"
    clean.mkdir()
    dirty.mkdir()
    for repo in (clean, dirty):
        init = subprocess.run(["git", "init"], cwd=repo, capture_output=True, text=True, check=False)
        assert init.returncode == 0, init.stderr
    assert ground_dirty(clean) is False
    (dirty / "note.txt").write_text("x", encoding="utf-8")
    assert ground_dirty(dirty) is True
    assert ground_dirty(tmp_path / "missing") is True

    def runner(argv: list[str]) -> str:
        assert argv[argv.index("--dirty") + 1] == "yes"
        return _decision()

    out = route_work(
        utterance="review the public page",
        job="review",
        runner=runner,
        dirty_probe=lambda: ground_dirty(dirty),
    )
    assert out["started"] is False


def test_unknown_job_coerces_to_dispatch() -> None:
    seen: list[str] = []

    def runner(argv: list[str]) -> str:
        seen.extend(argv)
        return json.dumps({"job": "dispatch", "refuse": False, "primary": "grok-local", "spawn": False, "reasons": []})

    out = route_work(
        utterance="do the public thing",
        job="not-a-real-job",
        runner=runner,
        dirty_probe=lambda: False,
    )
    assert seen[seen.index("--job") + 1] == "dispatch"
    assert out["started"] is False
    assert out["data_class"] == "public"
    assert "job-coerced-to-dispatch" in out["reasons"]


def test_empty_utterance_does_not_call_the_table() -> None:
    def runner(argv: list[str]) -> str:
        raise AssertionError(argv)

    out = route_work(utterance="   ", job="implement", runner=runner)
    assert out["ok"] is False
    assert out["started"] is False
    assert out["packet"] is False


def test_live_dispatch_table_answers_a_public_judgment() -> None:
    script = Path(r"C:\Users\frank\starlight\tools\dispatch-route.mjs")
    if not script.is_file():
        pytest.skip("estate dispatch table is not on this machine")
    out = route_work(
        utterance="review the public voice operator",
        job="judgment",
        script=script,
        dirty_probe=lambda: False,
    )
    assert out["packet"] is True
    assert out["started"] is False
    assert out["data_class"] == "public"
    assert out["primary"] == "claude-local"
    assert "did not start" in str(out["spoken"])
    unknown = route_work(
        utterance="review the voice operator",
        job="judgment",
        script=script,
        dirty_probe=lambda: False,
    )
    assert unknown["packet"] is True
    assert unknown["started"] is False
    assert unknown["refuse"] is True
    assert unknown["data_class"] == "unknown"
    assert "command" not in unknown
