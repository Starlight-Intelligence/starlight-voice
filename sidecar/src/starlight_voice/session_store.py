"""Local session store for the clap operator.

The estate vault stays where it is. This store is the voice process's own
record: what phase is live, the xAI conversation id for the next clap, and
a short transcript line. It never stores a token.
"""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path

from .config import repo_root

_LOCK = threading.Lock()
_ID = re.compile(r"^[A-Za-z0-9_.:-]{6,128}$")


def state_dir() -> Path:
    override = os.environ.get("STARLIGHT_VOICE_STATE_DIR", "").strip()
    return Path(override) if override else repo_root() / "memory" / "voice"


def live_path() -> Path:
    return state_dir() / "live.json"


def sessions_path() -> Path:
    return state_dir() / "sessions.jsonl"


def new_session_id() -> str:
    return uuid.uuid4().hex[:12]


def safe_conversation_id(value: object) -> str:
    text = str(value or "").strip()
    return text if _ID.fullmatch(text) else ""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def publish(phase: str, **fields: object) -> dict[str, object]:
    """Atomically replace live.json. Never raises into the listen loop."""

    record: dict[str, object] = {
        "phase": phase,
        "updated": _now(),
        "reload": "Stop this listen before the next one. A second start does not replace it.",
        **fields,
    }
    try:
        with _LOCK:
            _write(live_path(), json.dumps(record, ensure_ascii=False))
    except OSError:
        pass
    return record


def read_live() -> dict[str, object]:
    try:
        value = json.loads(live_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"phase": "stopped", "memory": "unavailable"}
    return value if isinstance(value, dict) else {"phase": "stopped", "memory": "unavailable"}


def append_event(kind: str, **fields: object) -> None:
    record = {"ts": _now(), "kind": kind, **fields}
    try:
        with _LOCK:
            path = sessions_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass


def read_sessions(limit: int = 20) -> list[dict]:
    try:
        lines = [line for line in sessions_path().read_text(encoding="utf-8").splitlines() if line.strip()]
    except OSError:
        return []
    rows: list[dict] = []
    for line in lines[-limit:]:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def last_conversation_id() -> str:
    for row in reversed(read_sessions(40)):
        found = safe_conversation_id(row.get("conversation_id"))
        if found:
            return found
    return ""


def note_conversation_id(event: dict) -> str:
    if event.get("type") != "conversation.created":
        return ""
    conversation = event.get("conversation")
    if isinstance(conversation, dict):
        found = safe_conversation_id(conversation.get("id"))
        if found:
            return found
    return safe_conversation_id(event.get("conversation_id"))


def prior_talk(limit: int = 6) -> str:
    lines = [
        str(row.get("line"))
        for row in read_sessions(40)
        if row.get("kind") == "transcript" and row.get("line")
    ]
    if not lines:
        return ""
    return " | ".join(line[:120] for line in lines[-limit:])


def _desk_files() -> list[Path]:
    override = os.environ.get("STARLIGHT_DESK_FILES", "").strip()
    if override:
        return [Path(part.strip()) for part in override.split(";") if part.strip()]
    root = Path(r"C:\Users\frank\starlight\command-center")
    return [root / "state.json", root / "OPS-DESK.md"]


def _snippet(path: Path, needle: str) -> str:
    try:
        raw = path.read_text(encoding="utf-8")[:64_000]
    except OSError:
        return ""
    folded = raw.lower()
    at = folded.find(needle)
    if at < 0:
        return ""
    start = max(0, at - 60)
    clip = " ".join(raw[start : at + 140].split())
    return clip[:180]


def estate_lookup(query: str) -> dict[str, object]:
    """Search the session and the desk files. No home walk, no web, no spawn."""

    text = " ".join(query.split())
    if len(text) < 2:
        return {
            "ok": False,
            "started": False,
            "spoken": "Nothing to look up.",
            "hits": [],
        }
    needle = text.lower()
    hits: list[dict[str, str]] = []
    for row in read_sessions(40):
        line = str(row.get("line") or "")
        if needle in line.lower():
            hits.append({"source": "session", "text": line[:180]})
        if len(hits) >= 5:
            break
    live = read_live()
    if needle in json.dumps(live).lower():
        hits.append(
            {
                "source": "live",
                "text": f"phase {live.get('phase')} {live.get('line') or ''}".strip()[:180],
            }
        )
    for path in _desk_files():
        if len(hits) >= 5:
            break
        clip = _snippet(path, needle)
        if clip:
            hits.append({"source": path.name, "text": clip})
    if not hits:
        spoken = "Nothing on the desk or the recent session matches that."
    else:
        spoken = f"I found {len(hits)} on the desk and the session. {hits[0]['text']}"
    return {"ok": bool(hits), "started": False, "spoken": spoken[:280], "hits": hits[:5]}


def recall_for_open() -> tuple[str, str]:
    """One vault recall at socket open. Empty when the gateway is down."""

    try:
        from .memory import MemoryGatewayClient

        client = MemoryGatewayClient.autodiscover()
        if not client.available():
            return "", "unavailable"
        block = client.as_context_block("Starlight voice operator current work", limit=4)
    except Exception:
        return "", "unavailable"
    if not block.strip():
        return "", "none"
    hits = block.count("\n- ")
    return block, f"{max(hits, 1)} hits"
