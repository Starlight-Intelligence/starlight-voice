import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from starlight_voice.meta_harness import route_work
from starlight_voice.x_voice import (
    audio_delta,
    function_output,
    note_transcript,
    parse_arguments,
    session_update,
    token_from_grok_auth,
)


def test_session_update_pins_grok_voice_and_the_harness_tool() -> None:
    message = session_update()
    session = message["session"]
    assert session["voice"]
    assert session["turn_detection"]["type"] == "server_vad"
    assert session["reasoning"]["effort"] == "none"
    assert session["audio"]["input"]["format"]["rate"] == 24000
    assert session["audio"]["output"]["format"]["rate"] == 24000
    assert session["tools"][0]["name"] == "meta_harness"
    assert "started is always false" in session["instructions"]


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
