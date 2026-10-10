import asyncio
import json
import os
import subprocess
import sys
import time
from types import SimpleNamespace

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


_WRITER = r'''
import importlib.util, json, os, sys, time
from pathlib import Path
spec = importlib.util.spec_from_file_location("observations", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
target = Path(sys.argv[2])
module.MAX_FILE_BYTES = int(sys.argv[3])
if len(sys.argv) > 4:
    ready, release = map(Path, sys.argv[4:6])
    original_open, original_os_open = Path.open, os.open
    def gate():
        ready.touch()
        deadline = time.monotonic() + 5
        while not release.exists():
            if time.monotonic() >= deadline:
                raise TimeoutError("Writer barrier timed out")
            time.sleep(.01)
    def path_open(path, mode="r", *args, **kwargs):
        handle = original_open(path, mode, *args, **kwargs)
        if path == target and "a" in mode:
            gate()
        return handle
    def os_open(path, flags, *args, **kwargs):
        fd = original_os_open(path, flags, *args, **kwargs)
        if Path(path) == target and flags & os.O_APPEND:
            gate()
        return fd
    Path.open, os.open = path_open, os_open
data = {"sequence": 1, "status": "completed", "interrupted": False,
        "response_created_ms": None, "first_scheduled_audio_ms": None,
        "response_done_ms": None, "token_counts": None}
try:
    module.append_observation(data, session_id="race-session", provider="grok",
                              model="test", voice="ara", tool_results={}, path=target)
    print(json.dumps({"stored": True}))
except OSError:
    print(json.dumps({"stored": False}))
'''


def _writer_args(path, cap):
    import starlight_voice.turn_observations as module

    return [sys.executable, "-c", _WRITER, module.__file__, str(path), str(cap)]


def _seed_history(path):
    from starlight_voice.turn_observations import append_observation

    append_observation(metadata(response_created_ms=None, first_scheduled_audio_ms=None,
                                response_done_ms=None, token_counts=None),
                       session_id="race-session", provider="grok", model="test", voice="ara",
                       tool_results={}, path=path)
    return path.read_bytes()


def test_separate_processes_cannot_both_spend_the_last_history_record(tmp_path):
    path = tmp_path / "turns.jsonl"
    old = _seed_history(path)
    cap = 2 * len(old) + 16
    release = tmp_path / "release"
    ready = [tmp_path / f"ready-{index}" for index in range(2)]
    workers = []
    try:
        for marker in ready:
            workers.append(subprocess.Popen([*_writer_args(path, cap), str(marker), str(release)],
                                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))
        deadline = time.monotonic() + 5
        while not all(marker.exists() for marker in ready):
            assert all(worker.poll() is None for worker in workers), "Writer exited before the barrier"
            assert time.monotonic() < deadline, "Writer barrier timed out"
            time.sleep(.01)
        release.touch()
        results = []
        for worker in workers:
            stdout, stderr = worker.communicate(timeout=5)
            assert worker.returncode == 0, stderr
            results.append(json.loads(stdout)["stored"])
        assert results.count(True) == 1
        assert path.stat().st_size <= cap
        assert path.read_bytes().startswith(old)
        assert len(path.read_bytes().splitlines()) == 2
    finally:
        release.touch()
        for worker in workers:
            if worker.poll() is None:
                worker.terminate()
                worker.communicate(timeout=5)


def test_other_process_lock_fails_without_waiting_or_writing_and_retry_recovers(tmp_path):
    path = tmp_path / "turns.jsonl"
    old = _seed_history(path)
    with path.open("rb+") as handle:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            result = subprocess.run(_writer_args(path, 2 * len(old) + 16), capture_output=True,
                                    text=True, timeout=5, check=True)
            assert json.loads(result.stdout) == {"stored": False}
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)
    assert path.read_bytes() == old
    result = subprocess.run(_writer_args(path, 2 * len(old) + 16), capture_output=True,
                            text=True, timeout=5, check=True)
    assert json.loads(result.stdout) == {"stored": True}
    assert len(path.read_bytes().splitlines()) == 2


def test_exact_byte_limit_has_no_platform_newline_overrun(tmp_path, monkeypatch):
    import starlight_voice.turn_observations as module

    fixed_time = module.datetime.now(module.UTC)
    monkeypatch.setattr(module, "datetime", SimpleNamespace(now=lambda _zone: fixed_time))
    path = tmp_path / "turns.jsonl"
    old = _seed_history(path)
    expected = old.replace(b"\r\n", b"\n")
    path.write_bytes(b"")
    monkeypatch.setattr(module, "MAX_FILE_BYTES", len(expected))
    _seed_history(path)
    assert path.stat().st_size == len(expected)
    assert path.read_bytes().endswith(b"\n") and b"\r\n" not in path.read_bytes()


def test_short_writes_stay_locked_until_the_whole_record_is_appended(tmp_path, monkeypatch):
    import starlight_voice.turn_observations as module

    write = module.os.write
    writes = []

    def short_write(fd, data):
        writes.append(len(data))
        return write(fd, data[:31])

    monkeypatch.setattr(module.os, "write", short_write)
    path = tmp_path / "turns.jsonl"
    _seed_history(path)
    assert len(writes) > 1
    assert json.loads(path.read_bytes())["schema"] == SCHEMA


def test_write_failure_closes_owned_descriptor_and_retry_preserves_old_history(tmp_path, monkeypatch):
    import starlight_voice.turn_observations as module

    path = tmp_path / "turns.jsonl"
    old = _seed_history(path)

    def unavailable(*_args):
        raise OSError("Injected disk error")

    with monkeypatch.context() as patch:
        patch.setattr(module.os, "write", unavailable)
        with pytest.raises(OSError):
            _seed_history(path)
    assert path.read_bytes() == old
    result = subprocess.run(_writer_args(path, 2 * len(old) + 16), capture_output=True,
                            text=True, timeout=5, check=True)
    assert json.loads(result.stdout) == {"stored": True}
    assert path.read_bytes().startswith(old)


@pytest.mark.skipif(os.name == "nt", reason="POSIX FIFO denial; Windows uses ordinary file handles")
def test_nonregular_fifo_target_is_rejected_without_waiting_for_a_reader(tmp_path):
    path = tmp_path / "turns.jsonl"
    os.mkfifo(path)
    result = subprocess.run(_writer_args(path, 4096), capture_output=True,
                            text=True, timeout=5, check=True)
    assert json.loads(result.stdout) == {"stored": False}
