"""Local voice-minute cap. The socket stays closed once today's cap is spent.

The October 2026 voice price used for the estimate is $0.08 per minute while
the socket is open. The ledger is an estimate, not the invoice. Search tools
are billed separately and are capped in the session.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from .session_store import state_dir

USD_PER_MINUTE = 0.08
DEFAULT_DAILY_MINUTES = 15
DAILY_MINUTE_CEILING = 60
WEB_SEARCH_CAP = 3
X_SEARCH_CAP = 1


def spend_path() -> Path:
    return state_dir() / "spend.jsonl"


def daily_cap_seconds() -> float:
    raw = os.environ.get("STARLIGHT_XVOICE_DAILY_MINUTES", str(DEFAULT_DAILY_MINUTES)).strip()
    try:
        minutes = float(raw)
    except ValueError:
        minutes = DEFAULT_DAILY_MINUTES
    minutes = min(max(minutes, 1), DAILY_MINUTE_CEILING)
    return minutes * 60


def _day(now: datetime | None = None) -> str:
    stamp = now or datetime.now(UTC)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return stamp.astimezone(UTC).date().isoformat()


def used_seconds(now: datetime | None = None) -> float:
    path = spend_path()
    if not path.is_file():
        return 0
    day = _day(now)
    total = 0.0
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return 0
    for line in lines[-400:]:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict) or row.get("day") != day:
            continue
        try:
            total += max(0.0, float(row.get("seconds") or 0))
        except (TypeError, ValueError):
            continue
    return total


def cap_label(used: float | None = None, now: datetime | None = None) -> str:
    spent = used_seconds(now) if used is None else used
    cap = daily_cap_seconds()
    return f"{spent / 60:.1f} of {cap / 60:.0f} min"


def open_budget(now: datetime | None = None, session_cap: float = 600) -> dict[str, object]:
    used = used_seconds(now)
    cap = daily_cap_seconds()
    left = max(0.0, cap - used)
    label = cap_label(used, now)
    if left < 1:
        return {
            "ok": False,
            "max_s": 0.0,
            "why": "daily cap",
            "label": label,
            "spoken": f"Daily voice cap is spent. {label}. The socket stays closed.",
        }
    max_s = min(session_cap, left)
    why = "daily cap" if max_s < session_cap - 1 else "session cap"
    return {"ok": True, "max_s": max_s, "why": why, "label": label, "spoken": ""}


def record_seconds(seconds: float, now: datetime | None = None) -> str:
    spent = max(0.0, float(seconds))
    stamp = now or datetime.now(UTC)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    row = {
        "day": _day(stamp),
        "at": stamp.astimezone(UTC).isoformat(),
        "seconds": round(spent, 1),
        "usd_estimate": round(spent / 60 * USD_PER_MINUTE, 4),
    }
    path = spend_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, separators=(",", ":")) + "\n")
    except OSError:
        return cap_label(now=stamp)
    return cap_label(now=stamp)
