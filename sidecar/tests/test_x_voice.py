import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from starlight_voice.meta_harness import route_work
from starlight_voice.spend import daily_cap_seconds, open_budget, record_seconds
from starlight_voice.x_voice import (
    audio_delta,
    function_output,
    key_from_infisical,
    note_transcript,
    parse_arguments,
    resolve_token,
    server_tool_hit,
    session_update,
    token_from_grok_auth,
)


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
    assert "web_search" in session["instructions"]


def test_account_login_beats_an_env_api_key(monkeypatch, tmp_path: Path) -> None:
    path = tmp_path / "auth.json"
    future = (datetime.now(UTC) + timedelta(minutes=5)).isoformat()
    path.write_text(json.dumps({"acct": {"key": "test-token-not-real", "expires_at": future}}), encoding="utf-8")
    monkeypatch.setenv("STARLIGHT_GROK_AUTH", str(path))
    monkeypatch.setenv("XAI_API_KEY", "openrouter-key-not-for-this-socket")
    monkeypatch.delenv("STARLIGHT_XVOICE_USE_API_KEY", raising=False)
    assert resolve_token() == "test-token-not-real"


def test_xai_shaped_env_key_is_used(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("STARLIGHT_GROK_AUTH", str(tmp_path / "missing.json"))
    monkeypatch.setenv("XAI_API_KEY", "xai-test-not-real")
    monkeypatch.setenv("STARLIGHT_XVOICE_USE_API_KEY", "0")
    assert resolve_token() is None
    monkeypatch.delenv("STARLIGHT_XVOICE_USE_API_KEY", raising=False)
    assert resolve_token() == "xai-test-not-real"


def test_infisical_rejects_a_non_xai_secret(monkeypatch) -> None:
    monkeypatch.setenv("STARLIGHT_XVOICE_INFISICAL", "1")
    assert key_from_infisical(lambda: "sk-or-not-for-this-socket") is None
    assert key_from_infisical(lambda: "xai-test-from-infisical") == "xai-test-from-infisical"


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
    record_seconds(15 * 60)
    budget = open_budget()
    assert budget["ok"] is False
    assert "stays closed" in str(budget["spoken"])
    monkeypatch.setenv("STARLIGHT_XVOICE_DAILY_MINUTES", "10000")
    assert daily_cap_seconds() == 60 * 60


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


def test_route_work_holds_the_seat(monkeypatch) -> None:
    def runner(argv: list[str]) -> str:
        assert "--job" in argv
        assert "judgment" in argv
        assert "--class" not in argv
        return json.dumps({
            "job": "judgment",
            "refuse": False,
            "primary": "claude-local",
            "spawn": True,
            "reasons": [],
            "next": "write GitHub packet",
        })

    out = route_work(utterance="review the voice operator", job="review", runner=runner)
    assert out["started"] is False
    assert out["primary"] == "claude-local"
    assert out["job"] == "judgment"
    assert out["table_would_spawn"] is True
    assert "claude-local" in str(out["spoken"])
    assert "did not start" in str(out["spoken"])


def test_unknown_job_coerces_to_dispatch() -> None:
    seen: list[str] = []

    def runner(argv: list[str]) -> str:
        seen.extend(argv)
        return json.dumps({"job": "dispatch", "refuse": False, "primary": "grok-local", "spawn": False, "reasons": []})

    out = route_work(utterance="do the thing", job="not-a-real-job", runner=runner)
    assert seen[seen.index("--job") + 1] == "dispatch"
    assert out["started"] is False
    assert "job-coerced-to-dispatch" in out["reasons"]


def test_empty_utterance_does_not_call_the_table() -> None:
    def runner(argv: list[str]) -> str:
        raise AssertionError(argv)

    out = route_work(utterance="   ", job="implement", runner=runner)
    assert out["ok"] is False
    assert out["started"] is False


def test_grok_auth_ignores_an_expired_token(tmp_path: Path) -> None:
    path = tmp_path / "auth.json"
    past = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    path.write_text(json.dumps({"acct": {"key": "not-a-real-token", "expires_at": past}}), encoding="utf-8")
    assert token_from_grok_auth(path) is None


def test_grok_auth_reads_a_live_token(tmp_path: Path) -> None:
    path = tmp_path / "auth.json"
    future = (datetime.now(UTC) + timedelta(minutes=5)).isoformat()
    path.write_text(json.dumps({"acct": {"key": "test-token-not-real", "expires_at": future}}), encoding="utf-8")
    assert token_from_grok_auth(path) == "test-token-not-real"


def test_live_dispatch_table_answers_judgment() -> None:
    script = Path(r"C:\Users\frank\starlight\tools\dispatch-route.mjs")
    if not script.is_file():
        pytest.skip("estate dispatch table is not on this machine")
    out = route_work(utterance="review the voice operator", job="judgment", script=script)
    assert out["started"] is False
    assert out["primary"] == "claude-local"
    assert out["refuse"] is False
    assert "did not start" in str(out["spoken"])
