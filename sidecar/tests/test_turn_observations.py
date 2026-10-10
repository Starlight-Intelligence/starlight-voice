import asyncio
import json
import time

import pytest

from starlight_voice.native_bridge import BridgeError, NativeBridge
from starlight_voice.turn_observations import SCHEMA, recent_observations, validate_observation


def metadata(**changes):
    return {"sequence": 1, "status": "completed", "interrupted": False,
            "response_created_ms": 100, "first_scheduled_audio_ms": 200,
            "response_done_ms": 150, "token_counts": {"input_tokens": 10, "output_tokens": 5}, **changes}


@pytest.fixture
def session(tmp_path):
    async def mint(*args):
        return {"value": "ephemeral-secret", "expires_at": time.time() + 60}

    path = tmp_path / "turns.jsonl"
    bridge = NativeBridge(mint=mint, observation_path=path)
    info = asyncio.run(bridge.start({"provider": "grok", "voice": "ara"}))
    return bridge, info, path


def observe(session, data=None, capability=None):
    bridge, info, _ = session
    return bridge.observe({"session_id": info["session_id"], "observation": data if data is not None else metadata()},
                          info["capability"] if capability is None else capability)


@pytest.mark.parametrize("changes", [
    {"transcript": "private utterance"}, {"audio": "AAAA"}, {"tool_arguments": {}},
    {"status": "ignore policy"}, {"status": []}, {"interrupted": 1}, {"sequence": True},
    {"sequence": 101}, {"response_done_ms": float("nan")}, {"response_done_ms": float("inf")},
    {"response_done_ms": -1}, {"response_done_ms": 99}, {"first_scheduled_audio_ms": True},
    {"token_counts": {"input_tokens": 10, "output_tokens": -1}},
    {"token_counts": {"input_tokens": 10, "output_tokens": 5, "prompt": "private"}},
])
def test_observations_reject_non_metadata_and_invalid_values(session, changes):
    with pytest.raises(BridgeError):
        observe(session, metadata(**changes))
    assert not session[2].exists()


def test_unknown_timings_cost_and_quality_remain_unknown(session):
    data = metadata(response_created_ms=None, first_scheduled_audio_ms=None, response_done_ms=None, token_counts=None)
    result = observe(session, data)
    assert result["stored"] and not result["acceptance_verified"]
    record = json.loads(session[2].read_text())
    assert record["schema"] == SCHEMA
    assert record["client_observation"] == data
    assert record["client_observation_verified"] is False
    assert record["first_playable_audio_ms"] is None and record["estimated_cost_usd"] is None
    assert all(value is None for value in record["quality"].values())
    assert record["configured_provider"] == "grok" and record["configured_voice"] == "ara"
    assert "ephemeral-secret" not in session[2].read_text() and session[1]["capability"] not in session[2].read_text()


def test_idempotent_retry_cannot_replace_or_duplicate_a_record(session):
    observe(session)
    assert observe(session)["duplicate"] is True
    with pytest.raises(BridgeError) as error:
        observe(session, metadata(status="failed"))
    assert error.value.status == 409
    assert len(session[2].read_text().splitlines()) == 1


def test_records_require_live_authority_and_stop_revokes_it(session):
    with pytest.raises(BridgeError) as error:
        observe(session, capability="wrong")
    assert error.value.status == 401
    bridge, info, path = session
    bridge.stop({"session_id": info["session_id"]}, info["capability"])
    with pytest.raises(BridgeError):
        observe(session)
    assert not path.exists()


def test_write_failure_reports_unavailable_and_allows_retry(session, monkeypatch):
    import starlight_voice.native_bridge as module

    writer = module.append_observation

    def unavailable(*args, **kwargs):
        raise OSError("private filesystem path")

    monkeypatch.setattr(module, "append_observation", unavailable)
    with pytest.raises(BridgeError) as error:
        observe(session)
    assert error.value.status == 503 and "private" not in str(error.value)
    assert session[0].session.observations == {}
    monkeypatch.setattr(module, "append_observation", writer)
    assert observe(session)["stored"]


def test_tool_status_counts_are_broker_observed_and_exclude_arguments(session):
    bridge, info, path = session
    result = asyncio.run(bridge.tool({"session_id": info["session_id"], "call_id": "call",
                                     "name": "dispatch_task", "arguments": {"query": "private task text"}},
                                    info["capability"]))
    assert result["status"] == "blocked"
    observe(session)
    record = json.loads(path.read_text())
    assert record["session_tool_result_counts"] == {"dispatch_task:blocked": 1}
    assert "private task text" not in path.read_text()
    assert record["quality"]["approval_compliant"] is None


def test_tail_reader_sanitizes_untrusted_records_and_keeps_unknowns(session):
    _, _, path = session
    observe(session)
    with path.open("a") as handle:
        handle.write(json.dumps({"schema": SCHEMA, "client_observation": metadata(status="private text")}) + "\n")
        handle.write('malformed\n')
    result = recent_observations(path=path)
    assert len(result) == 1
    assert result[0]["source"] == "unverified-client-observation"
    assert result[0]["first_playable_audio_ms"] is None
    assert "session_id" not in result[0] and "token_counts" not in result[0]


def test_fixed_storage_cap_preserves_existing_history(session, monkeypatch):
    import starlight_voice.turn_observations as module

    observe(session)
    old = session[2].read_bytes()
    monkeypatch.setattr(module, "MAX_FILE_BYTES", len(old))
    with pytest.raises(BridgeError):
        observe(session, metadata(sequence=2))
    assert session[2].read_bytes() == old


def test_validated_metadata_detaches_nested_caller_values():
    data = metadata()
    validated = validate_observation(data)
    data["token_counts"]["input_tokens"] = 999
    assert validated["token_counts"]["input_tokens"] == 10
