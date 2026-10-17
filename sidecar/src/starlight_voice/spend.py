"""Local voice-minute cap plus the xAI postpaid spending limit.

A reserve is written before the socket opens and counts until a settle
replaces it, so a crash still spends the reserved seconds. The October 2026
voice price used for the estimate is $0.08 per minute. The ledger is an
estimate, not the invoice. Search tools are billed separately.

The server gate reads the xAI management API. Docs checked 2026-10-17:
``GET https://management-api.x.ai/v1/billing/teams/{team_id}/postpaid/spending-limits``
stops postpaid use at ``effectiveSl``. That limit does not cap prepaid
credit. A purchase amount is negative, and the sample prepaid ``total`` is
negative while credit remains.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import uuid
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

from .config import load_local_env
from .session_store import state_dir

USD_PER_MINUTE = 0.08
DEFAULT_DAILY_MINUTES = 15
DAILY_MINUTE_CEILING = 60
WEB_SEARCH_CAP = 3
X_SEARCH_CAP = 1
MANAGEMENT_BASE = "https://management-api.x.ai"
_LOCK_WAIT_S = 3.0
_LOCK_STALE_S = 5.0


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


def _aware(now: datetime | None = None) -> datetime:
    stamp = now or datetime.now(UTC)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return stamp.astimezone(UTC)


def _day(now: datetime | None = None) -> str:
    return _aware(now).date().isoformat()


def _label(used: float, cap: float) -> str:
    return f"{used / 60:.1f} of {cap / 60:.0f} min"


def _shut(why: str) -> dict[str, object]:
    spoken = {
        "corrupt": "Voice ledger is unreadable. The socket stays closed.",
        "locked": "Voice ledger is locked. The socket stays closed.",
        "ledger": "Voice ledger could not be written. The socket stays closed.",
    }.get(why, "Voice ledger could not be read. The socket stays closed.")
    return {"ok": False, "why": why, "max_s": 0.0, "label": "unreadable", "spoken": spoken}


def _acquire(lock_path: Path) -> int:
    deadline = time.monotonic() + _LOCK_WAIT_S
    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
        except FileExistsError:
            try:
                age = time.time() - lock_path.stat().st_mtime
            except OSError:
                age = 0
            if age > _LOCK_STALE_S:
                try:
                    lock_path.unlink()
                except OSError:
                    pass
                continue
            if time.monotonic() > deadline:
                raise TimeoutError("spend ledger is locked") from None
            time.sleep(0.02)
            continue
        os.write(fd, str(os.getpid()).encode("ascii"))
        return fd


def _read_rows(path: Path) -> list[dict] | None:
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    rows: list[dict] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            return None
        if not _valid_row(row):
            return None
        rows.append(row)
    return rows


def _valid_row(row: object) -> bool:
    if not isinstance(row, dict):
        return False
    if row.get("kind") not in {"reserve", "settle"}:
        return False
    day = row.get("day")
    row_id = row.get("id")
    if not isinstance(day, str) or len(day) != 10 or not isinstance(row_id, str) or not row_id:
        return False
    try:
        seconds = float(row.get("seconds"))
    except (TypeError, ValueError):
        return False
    return seconds >= 0 and seconds != float("inf")


def _used(rows: list[dict], day: str) -> float:
    reserved: dict[str, float] = {}
    settled: dict[str, float] = {}
    for row in rows:
        if row.get("day") != day:
            continue
        bucket = settled if row["kind"] == "settle" else reserved
        bucket[str(row["id"])] = float(row["seconds"])
    total = 0.0
    for row_id in set(reserved) | set(settled):
        total += settled[row_id] if row_id in settled else reserved[row_id]
    return total


def _append(path: Path, row: dict) -> bool:
    try:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, separators=(",", ":")) + "\n")
            handle.flush()
    except OSError:
        return False
    return True


def _with_rows(mutator):
    path = spend_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return {"ok": False, "why": "ledger"}
    lock_path = path.with_name(path.name + ".lock")
    try:
        fd = _acquire(lock_path)
    except TimeoutError:
        return {"ok": False, "why": "locked"}
    try:
        rows = _read_rows(path)
        if rows is None:
            return {"ok": False, "why": "corrupt"}
        return mutator(path, rows)
    finally:
        os.close(fd)
        try:
            lock_path.unlink()
        except OSError:
            pass


def used_seconds(now: datetime | None = None) -> float | None:
    """Seconds charged today. None means the ledger cannot be trusted."""

    state = _with_rows(lambda _path, rows: {"ok": True, "seconds": _used(rows, _day(now))})
    if not state.get("ok"):
        return None
    return float(state["seconds"])


def cap_label(used: float | None = None, now: datetime | None = None) -> str:
    spent = used_seconds(now) if used is None else used
    if spent is None:
        return "unreadable"
    return _label(spent, daily_cap_seconds())


def open_budget(now: datetime | None = None, session_cap: float = 600) -> dict[str, object]:
    state = _with_rows(lambda _path, rows: {"ok": True, "seconds": _used(rows, _day(now))})
    if not state.get("ok"):
        return _shut(str(state.get("why") or "ledger"))
    used = float(state["seconds"])
    cap = daily_cap_seconds()
    left = max(0.0, cap - used)
    label = _label(used, cap)
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


def reserve(seconds: float, now: datetime | None = None) -> dict[str, object]:
    """Hold ``seconds`` against today's cap. The hold stands until :func:`settle`."""

    stamp = _aware(now)
    day = _day(stamp)
    want = round(max(0.0, float(seconds)), 1)
    cap = daily_cap_seconds()

    def write(path: Path, rows: list[dict]) -> dict[str, object]:
        used = _used(rows, day)
        label = _label(used, cap)
        if want < 1 or used + want > cap + 1e-6:
            return {
                "ok": False,
                "why": "daily cap",
                "max_s": max(0.0, cap - used),
                "label": label,
                "spoken": f"Daily voice cap is spent. {label}. The socket stays closed.",
            }
        row_id = uuid.uuid4().hex
        row = {
            "v": 1,
            "day": day,
            "at": stamp.isoformat(),
            "kind": "reserve",
            "id": row_id,
            "seconds": want,
        }
        if not _append(path, row):
            return _shut("ledger")
        return {
            "ok": True,
            "id": row_id,
            "seconds": want,
            "why": "reserved",
            "label": _label(used + want, cap),
            "spoken": "",
        }

    state = _with_rows(write)
    if state.get("spoken") is None:
        return _shut(str(state.get("why") or "ledger"))
    return state


def settle(reservation_id: str, seconds: float, now: datetime | None = None) -> str:
    """Replace a reserve with the seconds actually used. A missing reserve still counts."""

    stamp = _aware(now)
    actual = round(max(0.0, float(seconds)), 1)

    def write(path: Path, rows: list[dict]) -> dict[str, object]:
        day = _day(stamp)
        for row in rows:
            if row.get("id") == reservation_id and row.get("kind") == "reserve":
                day = str(row["day"])
                break
        row = {
            "v": 1,
            "day": day,
            "at": stamp.isoformat(),
            "kind": "settle",
            "id": reservation_id,
            "seconds": actual,
        }
        if not _append(path, row):
            return {"ok": False, "label": "unreadable"}
        rows.append(row)
        return {"ok": True, "label": _label(_used(rows, _day(stamp)), daily_cap_seconds())}

    state = _with_rows(write)
    return str(state.get("label") or "unreadable")


def _cents(value: object) -> int | None:
    if isinstance(value, dict):
        value = value.get("val")
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        sign = 1
        if text.startswith("+"):
            text = text[1:]
        elif text.startswith("-"):
            sign = -1
            text = text[1:]
        if text.isdigit():
            return sign * int(text)
    return None


def _management_get(path: str, key: str) -> dict:
    if not path.startswith("/") or "\\" in path:
        raise ValueError("path")
    request = Request(
        MANAGEMENT_BASE + path,
        headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
        method="GET",
    )
    try:
        with urlopen(request, timeout=10) as response:
            raw = response.read(1_000_000)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"status={exc.code}") from None
    except urllib.error.URLError:
        raise RuntimeError("unreachable") from None
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        raise RuntimeError("bad-body") from None
    if not isinstance(payload, dict):
        raise RuntimeError("bad-body")
    return payload


def _gate_shut(why: str, spoken: str) -> dict[str, object]:
    return {"ok": False, "why": why, "spoken": spoken}


def server_spend_gate(get_json=None) -> dict[str, object]:
    """Refuse to open unless xAI reports a spending limit that still has room.

    ``get_json(path) -> dict`` is the test seam. It must not receive the key.
    """

    load_local_env()
    key = os.environ.get("XAI_MANAGEMENT_KEY", "").strip()
    team = os.environ.get("XAI_TEAM_ID", "").strip()
    if not key or not team:
        return _gate_shut(
            "xai-limit-unconfigured",
            "xAI spending limit is not configured. The socket stays closed.",
        )
    fetch = get_json or (lambda path: _management_get(path, key))
    team_q = quote(team, safe="")
    limit_path = f"/v1/billing/teams/{team_q}/postpaid/spending-limits"
    preview_path = f"/v1/billing/teams/{team_q}/postpaid/invoice/preview"
    balance_path = f"/v1/billing/teams/{team_q}/prepaid/balance"
    try:
        limits = fetch(limit_path)
        preview = fetch(preview_path)
    except (OSError, RuntimeError, TypeError, ValueError):
        return _gate_shut(
            "xai-limit-unread",
            "xAI spending limit could not be read. The socket stays closed.",
        )
    if not isinstance(limits, dict) or not isinstance(preview, dict):
        return _gate_shut(
            "xai-limit-unread",
            "xAI spending limit could not be read. The socket stays closed.",
        )
    limits_body = limits.get("spendingLimits")
    effective = _cents(limits_body.get("effectiveSl") if isinstance(limits_body, dict) else None)
    if effective is None or effective < 0:
        return _gate_shut(
            "xai-limit-missing",
            "xAI has no spending limit. The socket stays closed.",
        )
    if effective == 0:
        try:
            prepaid = fetch(balance_path)
        except (OSError, RuntimeError, TypeError, ValueError):
            return _gate_shut(
                "xai-limit-unread",
                "xAI spending limit could not be read. The socket stays closed.",
            )
        total = _cents(prepaid.get("total") if isinstance(prepaid, dict) else None)
        if total is None or total >= 0:
            return _gate_shut(
                "xai-prepaid-empty",
                "xAI prepaid balance is not available. The socket stays closed.",
            )
        return {"ok": True, "why": "xai-prepaid", "spoken": ""}
    invoice = preview.get("coreInvoice") if isinstance(preview.get("coreInvoice"), dict) else {}
    used = _cents(invoice.get("amountBeforeVatLimited"))
    if used is None:
        used = _cents(invoice.get("amountBeforeVat"))
    if used is None or used < 0:
        return _gate_shut(
            "xai-limit-unread",
            "xAI spending limit could not be read. The socket stays closed.",
        )
    if used >= effective:
        return _gate_shut(
            "xai-limit-reached",
            "xAI spending limit is reached. The socket stays closed.",
        )
    return {"ok": True, "why": "xai-limit", "spoken": ""}
