"""Operator results the live face can show.

A tab or the desktop opens on this machine. A seat handoff checks the local
login and leaves ``started`` false. It does not spawn a harness.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from .meta_harness import ground_class, route_work
from .session_store import estate_lookup

_SEATS = {
    "grok-local": "Grok CLI",
    "github": "GitHub",
}
_ACCOUNT = re.compile(r"account\s+(\S+)")


def lookup_memory(query: str) -> dict[str, object]:
    """Search the desk and the recent session. The search does not spawn."""

    found = estate_lookup(query)
    target = " ".join(str(query or "").split())
    spoken = str(found.get("spoken") or "")
    if target and target.lower() not in spoken.lower():
        spoken = f"Memory {target}. {spoken}".strip()
    found["target"] = target
    found["spoken"] = spoken[:280]
    found["started"] = False
    found["ran"] = True
    return found


def _launch(target: str) -> None:
    if os.name != "nt":
        raise OSError("no local opener")
    os.startfile(target)  # type: ignore[attr-defined]


def _http_target(name: str) -> str:
    if not name.startswith(("http://", "https://")):
        return ""
    if any(mark in name for mark in ("\n", "\r", " ", '"', "'")):
        return ""
    return name


def _desktop_folder(name: str) -> Path | None:
    direct = Path(name)
    if direct.is_dir():
        return direct
    desktop = Path.home() / "Desktop"
    if desktop.is_dir():
        return desktop
    return None


def open_named(kind: str, target: str) -> dict[str, object]:
    """Open a tab URL or the desktop folder. ``started`` stays false."""

    label = kind if kind in {"tab", "desktop"} else ""
    name = " ".join(str(target or "").split())[:180]
    held = f"Held. {label} {name}. I did not open it."
    if not label or not name:
        return {
            "ok": False,
            "started": False,
            "ran": False,
            "kind": label,
            "target": "",
            "spoken": "No target to open.",
        }
    if label == "tab":
        url = _http_target(name)
        if not url:
            return {"ok": False, "started": False, "ran": False, "kind": label, "target": name, "spoken": held}
        try:
            _launch(url)
        except OSError:
            return {"ok": False, "started": False, "ran": False, "kind": label, "target": name, "spoken": held}
        return {
            "ok": True,
            "started": False,
            "ran": True,
            "opened": True,
            "kind": label,
            "target": name,
            "spoken": f"Opened tab {name}.",
        }
    folder = _desktop_folder(name)
    if folder is None:
        return {"ok": False, "started": False, "ran": False, "kind": label, "target": name, "spoken": held}
    try:
        _launch(str(folder))
    except OSError:
        return {"ok": False, "started": False, "ran": False, "kind": label, "target": name, "spoken": held}
    return {
        "ok": True,
        "started": False,
        "ran": True,
        "opened": True,
        "kind": label,
        "target": name,
        "path": str(folder),
        "spoken": f"Opened desktop {name}.",
    }


def _github_login() -> tuple[bool, str]:
    """Read-only ``gh auth status``. No repo write, merge, or send."""

    try:
        proc = subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False, ""
    match = _ACCOUNT.search(f"{proc.stdout}\n{proc.stderr}")
    if proc.returncode != 0 or match is None:
        return True, ""
    return True, match.group(1)


def _grok_source() -> str:
    from .x_voice import token_status

    status = token_status()
    if not status.get("present"):
        return ""
    return str(status.get("source") or "")


def hold_handoff(utterance: str, seat: str) -> dict[str, object]:
    """Record one seat. ``started`` stays false. The dispatch runner is in-process."""

    text = " ".join(str(utterance or "").split())
    primary = " ".join(str(seat or "").split())
    label = _SEATS.get(primary, primary)
    if not text or not primary:
        return {
            "ok": False,
            "started": False,
            "ran": False,
            "primary": "",
            "target": "",
            "spoken": "No seat to hold.",
        }

    def runner(argv: list[str]) -> str:
        del argv
        return json.dumps(
            {
                "job": "dispatch",
                "primary": primary,
                "refuse": False,
                "spawn": False,
                "reasons": [],
                "next": "",
            }
        )

    if ground_class(text) is not None:
        route_work(utterance=text, job="dispatch", dirty_probe=lambda: False, runner=runner)

    account = ""
    ran = False
    if primary == "github":
        ran, account = _github_login()
        if account:
            spoken = f"{text} goes to GitHub account {account}. Held. I did not start it."
        else:
            spoken = f"{text} goes to GitHub. Held. I did not start it."
    elif primary == "grok-local":
        account = _grok_source()
        ran = True
        if account:
            spoken = f"{text} goes to Grok CLI ({account}). Held. I did not start it."
        else:
            spoken = f"{text} goes to Grok CLI. Held. I did not start it."
    else:
        spoken = f"{text} goes to {label}. Held. I did not start it."
    return {
        "ok": True,
        "started": False,
        "ran": ran,
        "primary": primary,
        "target": label,
        "account": account,
        "spoken": spoken,
        "packet": True,
    }
