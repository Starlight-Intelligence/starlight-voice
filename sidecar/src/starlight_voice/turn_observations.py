"""Bounded local turn observations, never fabricated completion receipts.

Only enum/count/timing metadata is accepted. Browser observations are unverified;
audible latency, dollars and quality verdicts stay unknown. No audio, text, tool
arguments, provider response IDs or credentials enter this append-only file.
"""

from __future__ import annotations

import json
import math
import os
import stat
import threading
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

SCHEMA = "voice.turn-observation.v1"
STATUSES = {"completed", "cancelled", "failed", "incomplete", "unknown", "stopped"}
TIMINGS = {"response_created_ms", "first_scheduled_audio_ms", "response_done_ms"}
FIELDS = {"sequence", "status", "interrupted", "token_counts", *TIMINGS}
MAX_FILE_BYTES = 32 * 1024 * 1024
_lock = threading.Lock()


@contextmanager
def _file_lock(fd: int):
    # Nonblocking locks keep a competing recorder off the conversation's hot path.
    if os.name == "nt":
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        try:
            yield
        finally:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)


def validate_observation(data: dict) -> dict:
    if not isinstance(data, dict) or set(data) != FIELDS:
        raise ValueError("Only bounded turn metadata is accepted")
    sequence = data["sequence"]
    if type(sequence) is not int or not 1 <= sequence <= 100:
        raise ValueError("Turn sequence must be between 1 and 100")
    if not isinstance(data["status"], str) or data["status"] not in STATUSES:
        raise ValueError("Unknown turn status")
    if type(data["interrupted"]) is not bool:
        raise ValueError("Interruption must be a boolean")
    for key in TIMINGS:
        value = data[key]
        if value is not None and (type(value) not in {int, float} or not math.isfinite(value) or not 0 <= value <= 300000):
            raise ValueError("Timing must be unknown or a finite session-relative duration")
    created, scheduled, done = (data[key] for key in ("response_created_ms", "first_scheduled_audio_ms", "response_done_ms"))
    if created is not None and any(value is not None and value < created for value in (scheduled, done)):
        raise ValueError("Turn timing order is invalid")
    tokens = data["token_counts"]
    if tokens is not None and (
        not isinstance(tokens, dict) or set(tokens) != {"input_tokens", "output_tokens"}
        or any(type(value) is not int or not 0 <= value <= 10**7 for value in tokens.values())
    ):
        raise ValueError("Only bounded token counts are accepted")
    # Detach caller-owned containers before fingerprinting/writing them.
    return json.loads(json.dumps(data, allow_nan=False))


def default_path() -> Path:
    from .cognition.ledger import runs_path

    return runs_path().with_name("turn-observations.jsonl")


def append_observation(metadata: dict, *, session_id: str, provider: str, model: str,
                       voice: str, tool_results: dict, path: Path | None = None) -> None:
    target = path if path is not None else default_path()
    record = {
        "schema": SCHEMA, "recorded_at": datetime.now(UTC).isoformat(), "session_id": session_id,
        "configured_provider": provider, "configured_model": model, "configured_voice": voice,
        "client_observation": validate_observation(metadata), "client_observation_verified": False,
        "session_tool_result_counts": tool_results, "tool_counts_source": "local-broker",
        "first_playable_audio_ms": None, "estimated_cost_usd": None,
        "quality": {"tool_selection_correct": None, "privacy_compliant": None, "approval_compliant": None},
        "acceptance_verified": False,
    }
    line = (json.dumps(record, allow_nan=False, separators=(",", ":")) + "\n").encode("utf-8")
    with _lock:
        if target.is_symlink():
            raise OSError("Turn history target unavailable")
        target.parent.mkdir(parents=True, exist_ok=True)
        flags = (os.O_APPEND | os.O_CREAT | os.O_WRONLY | getattr(os, "O_BINARY", 0)
                 | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        fd = os.open(target, flags, 0o600)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise OSError("Turn history target unavailable")
            with _file_lock(fd):
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_size + len(line) > MAX_FILE_BYTES:
                    raise OSError("Turn history storage limit reached or target unavailable")
                remaining = memoryview(line)
                while remaining:
                    written = os.write(fd, remaining)
                    if written <= 0:
                        raise OSError("Turn history write unavailable")
                    remaining = remaining[written:]
        finally:
            os.close(fd)


def recent_observations(limit: int = 5, *, path: Path | None = None) -> list[dict]:
    """Bounded tail read; disclose only typed summaries, never raw stored dictionaries."""
    target = path if path is not None else default_path()
    try:
        with target.open("rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            start = max(0, size - 64 * 1024)
            handle.seek(start)
            lines = handle.read(64 * 1024).splitlines()
            if start:
                lines = lines[1:]
        records = []
        for line in reversed(lines):
            try:
                record = json.loads(line)
                observation = validate_observation(record["client_observation"])
                if record.get("schema") != SCHEMA:
                    continue
                records.append({
                    "source": "unverified-client-observation", "status": observation["status"],
                    "interrupted": observation["interrupted"],
                    "first_scheduled_audio_ms": observation["first_scheduled_audio_ms"],
                    "first_playable_audio_ms": None, "estimated_cost_usd": None,
                    "acceptance_verified": False,
                })
                if len(records) >= min(20, max(1, limit)):
                    break
            except (ValueError, TypeError, KeyError):
                continue
        return records
    except OSError:
        return []
