"""Ask the Starlight meta harness which seat owns one spoken job.

The harness is the estate dispatch table (`tools/dispatch-route.mjs`).
This module returns that decision. It does not spawn a CLI, claim a
worktree, or open a second orchestrator.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

JOBS = (
    "dispatch",
    "judgment",
    "implement",
    "cheap-async-pr",
    "daily-free",
    "integrate",
    "checker",
    "visual",
    "site-review",
    "content",
)

_ALIASES = {
    "code": "implement",
    "build": "implement",
    "review": "judgment",
    "judge": "judgment",
    "route": "dispatch",
    "merge": "integrate",
    "check": "checker",
    "design": "visual",
    "site": "site-review",
    "write": "content",
    "doc": "cheap-async-pr",
    "docs": "cheap-async-pr",
    "free": "daily-free",
}

_CLASSES = {"public", "secret", "money", "vault", "identity", "canon"}

_DEFAULT_SCRIPT = Path(r"C:\Users\frank\starlight\tools\dispatch-route.mjs")


def dispatch_script() -> Path:
    override = os.environ.get("STARLIGHT_DISPATCH_ROUTE", "").strip()
    return Path(override) if override else _DEFAULT_SCRIPT


def normalize_job(job: str) -> tuple[str, bool]:
    raw = " ".join(job.lower().replace("_", "-").split())
    if raw in JOBS:
        return raw, False
    mapped = _ALIASES.get(raw)
    if mapped:
        return mapped, False
    return "dispatch", True


def normalize_class(data_class: str) -> str:
    raw = data_class.strip().lower().replace("_", "-")
    if raw in {"", "public", "none"}:
        return "public"
    if raw == "private-canon":
        return "canon"
    return raw if raw in _CLASSES else "public"


def route_work(
    *,
    utterance: str,
    job: str,
    data_class: str = "public",
    dirty: bool = False,
    runner=None,
    script: Path | None = None,
) -> dict[str, object]:
    """Return the seat for this utterance. ``started`` is always false."""

    text = " ".join(utterance.split())
    if not text:
        return {
            "ok": False,
            "started": False,
            "spoken": "No request to route.",
            "job": "dispatch",
            "primary": "",
            "refuse": True,
            "reasons": ["empty-utterance"],
        }

    resolved_job, job_coerced = normalize_job(job)
    resolved_class = normalize_class(data_class)
    target = script or dispatch_script()
    argv = [
        "node",
        str(target),
        "--job",
        resolved_job,
        "--dirty",
        "yes" if dirty else "no",
        "--laptop-off",
        "no",
        "--json",
    ]
    if resolved_class != "public":
        argv.extend(["--class", resolved_class])

    try:
        raw = (runner or _node)(argv)
        decision = json.loads(raw)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError, RuntimeError) as exc:
        return {
            "ok": False,
            "started": False,
            "spoken": "Meta harness did not answer. I did not start anything.",
            "job": resolved_job,
            "primary": "",
            "refuse": True,
            "reasons": [type(exc).__name__],
            "utterance": text,
        }

    if not isinstance(decision, dict):
        return {
            "ok": False,
            "started": False,
            "spoken": "Meta harness returned an unreadable decision.",
            "job": resolved_job,
            "primary": "",
            "refuse": True,
            "reasons": ["bad-decision"],
            "utterance": text,
        }

    primary = str(decision.get("primary") or "")
    refuse = bool(decision.get("refuse"))
    reasons = [str(item) for item in decision.get("reasons") or []]
    if job_coerced:
        reasons = ["job-coerced-to-dispatch", *reasons]
    spoken = _spoken(resolved_job, primary, refuse, reasons)
    return {
        "ok": not refuse and bool(primary),
        "started": False,
        "spoken": spoken,
        "job": str(decision.get("job") or resolved_job),
        "primary": primary,
        "refuse": refuse,
        "reasons": reasons,
        "next": str(decision.get("next") or ""),
        "data_class": resolved_class,
        "utterance": text,
        "table_would_spawn": bool(decision.get("spawn")),
    }


def _spoken(job: str, primary: str, refuse: bool, reasons: list[str]) -> str:
    if refuse or not primary:
        why = reasons[0] if reasons else "refused"
        return f"Held. {job} did not get a seat ({why}). I did not start anything."
    return f"{job} goes to {primary}. Held. I did not start it."


def _node(argv: list[str]) -> str:
    proc = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        timeout=20,
        shell=False,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError("dispatch-route failed")
    return proc.stdout
