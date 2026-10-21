"""Ara's door to the Kernel's triage command (frankxai/starlight-kernel#4).

Read-only calls plus one decide call per spoken decision. The child gets a minimal environment, so
no xAI, management or tool-plane credential reaches it.
"""

from __future__ import annotations

import re

import json
import os
import subprocess
from pathlib import Path

TIMEOUT_S = 12
_PASS_ENV = (
    "PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "TEMP", "TMP", "HOME", "USERPROFILE",
    "APPDATA", "LOCALAPPDATA", "STARLIGHT_KERNEL_HOME", "GH_CONFIG_DIR",
)
_CANDIDATES = (
    Path.home() / "starlight" / "repos" / "starlight-kernel" / "bin" / "triage.mjs",
    Path.home() / "starlight" / "repos" / "starlight-kernel" / ".claude" / "worktrees" / "agent+claude+kernel-triage" / "bin" / "triage.mjs",
)
ACTIONS = ("keep", "close", "review", "handoff")


def triage_bin() -> Path | None:
    override = os.environ.get("STARLIGHT_TRIAGE_BIN", "").strip()
    if override:
        path = Path(override)
        return path if path.is_file() else None
    return next((path for path in _CANDIDATES if path.is_file()), None)


def child_env() -> dict[str, str]:
    return {name: os.environ[name] for name in _PASS_ENV if os.environ.get(name)}


def _run(args: list[str], runner=None) -> dict:
    script = triage_bin()
    if script is None:
        return {"ok": False, "started": False, "spoken": "Triage is not installed on this machine."}
    command = ["node", str(script), *args, "--json"]
    run = runner or (lambda cmd: subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_S, env=child_env(), check=False))
    try:
        done = run(command)
    except (OSError, subprocess.TimeoutExpired):
        return {"ok": False, "started": False, "spoken": "Triage did not answer."}
    try:
        out = json.loads(done.stdout or "{}")
    except json.JSONDecodeError:
        return {"ok": False, "started": False, "spoken": "Triage gave an answer I could not read."}
    out["exit"] = done.returncode
    return out


def next_items(count: int = 3, runner=None) -> dict[str, object]:
    count = max(1, min(int(count or 3), 5))
    out = _run(["next", "--count", str(count)], runner)
    # Only named fields reach the model, whatever the command printed.
    if out.get("error") or not isinstance(out.get("spoken"), str):
        return {"ok": False, "started": False, "spoken": str(out.get("error") or "Triage gave no queue.")[:200]}
    return {"ok": True, "started": False, "remaining": int(out.get("remaining") or 0), "spoken": out["spoken"][:900]}


def ask(ref: str, action: str, runner=None) -> dict[str, object]:
    """First half of a decision: the read-back. The token stays with the caller, never the model."""

    ref = str(ref).strip()
    if action not in ACTIONS or not ref.isdigit():
        return {"ok": False, "started": False, "spoken": "Say the item number and keep, close, review or hand off."}
    out = _run(["decide", ref, action], runner)
    if not out.get("pending"):
        return {"ok": False, "started": False, "spoken": str(out.get("error") or "I could not read that item back.")[:200]}
    # The PR number is what Ara must say aloud for the read-back to count (consent.heard_readback).
    number = re.search(r"#(\d+)@", str(out.get("key") or ""))
    return {
        "ok": True, "started": False, "readback": str(out["readback"]), "token": str(out["token"]),
        "ref": ref, "action": action, "number": number.group(1) if number else "",
    }


def record(ref: str, action: str, token: str, runner=None) -> dict[str, object]:
    """Second half, called by the listener after Frank's own yes. Never exposed as a model tool."""

    if action not in ACTIONS or not str(ref).isdigit() or not str(token).isalnum():
        return {"ok": False, "started": False, "spoken": "The decision was not recorded."}
    # The Kernel checks the token against the item and action it read back, so a queue rebuilt in
    # between cannot move the yes onto a different PR.
    out = _run(["decide", str(ref), action, "--token", str(token), "--source", "voice"], runner)
    if out.get("exit") == 0 and out.get("seq"):
        return {"ok": True, "started": False, "seq": out.get("seq"), "spoken": str(out.get("spoken") or "Recorded.")[:240]}
    return {"ok": False, "started": False, "spoken": str(out.get("error") or "The decision was not recorded.")[:200]}
