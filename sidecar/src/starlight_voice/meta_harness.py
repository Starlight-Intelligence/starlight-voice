"""Ask the Starlight meta harness which seat owns one spoken job.

The harness is the estate dispatch table (`tools/dispatch-route.mjs`).
This module returns that decision. It does not spawn a CLI, claim a
worktree, or open a second orchestrator.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from .config import repo_root

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


_MARKERS = (
    ("money", re.compile(r"\b(invoice|payment|stripe|billing|bank|wallet|price|spend|payroll)\b", re.I)),
    ("secret", re.compile(r"\b(secret|api[-_ ]?key|password|credential|token|private key)\b", re.I)),
    ("vault", re.compile(r"\b(vault|infisical)\b", re.I)),
    ("identity", re.compile(r"\b(passport|identity|ssn)\b", re.I)),
    ("canon", re.compile(r"\b(canon|private[- ]canon|lore)\b", re.I)),
)
_PUBLIC = re.compile(r"\bpublic\b", re.I)


def normalize_class(data_class: str | None) -> str | None:
    """Known class, or None. Unknown is not public."""

    if data_class is None:
        return None
    raw = data_class.strip().lower().replace("_", "-")
    if raw == "private-canon":
        return "canon"
    if raw in _CLASSES:
        return raw
    return None


def ground_class(utterance: str) -> str | None:
    """Class from the utterance alone. No match is unknown, not public."""

    hits = [name for name, pattern in _MARKERS if pattern.search(utterance)]
    if len(hits) == 1:
        return hits[0]
    if hits:
        return None
    if _PUBLIC.search(utterance):
        return "public"
    return None


def resolve_class(utterance: str, claimed: object = None) -> str | None:
    """Class from the utterance only. A claim cannot set or raise it."""

    grounded = ground_class(utterance)
    raw = "" if claimed is None else str(claimed).strip()
    if not raw:
        return grounded
    known = normalize_class(raw)
    if known is None or grounded is None:
        return None
    if known == grounded or known == "public":
        return grounded
    return None


def ground_dirty(root: Path | None = None) -> bool:
    """True when git status is non-empty, or when git cannot be read."""

    target = root or repo_root()
    try:
        proc = subprocess.run(
            ["git", "-C", str(target), "status", "--porcelain=v1"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return True
    if proc.returncode != 0:
        return True
    return bool(proc.stdout.strip())


def route_work(
    *,
    utterance: str,
    job: str,
    claimed_class: object = None,
    dirty_probe=None,
    runner=None,
    script: Path | None = None,
) -> dict[str, object]:
    """Emit one dispatch packet. ``started`` is always false. No approval gate."""

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
            "packet": False,
        }

    resolved_job, job_coerced = normalize_job(job)
    resolved_class = resolve_class(text, claimed_class)
    if dirty_probe is None:
        is_dirty = ground_dirty()
    else:
        try:
            is_dirty = bool(dirty_probe())
        except (OSError, RuntimeError, TypeError, ValueError):
            is_dirty = True
    target = script or dispatch_script()
    argv = [
        "node",
        str(target),
        "--job",
        resolved_job,
        "--dirty",
        "yes" if is_dirty else "no",
        "--laptop-off",
        "no",
        "--json",
    ]
    # Unknown is not sent as public. The table has no unknown class, so the
    # packet goes out without --class and the result below refuses it.
    if resolved_class not in {None, "public"}:
        argv.extend(["--class", resolved_class])

    decision: dict | None
    try:
        raw = (runner or _node)(argv)
        parsed = json.loads(raw)
        decision = parsed if isinstance(parsed, dict) else None
        packet_error = None if decision is not None else "bad-decision"
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError, RuntimeError) as exc:
        decision = None
        packet_error = type(exc).__name__

    if decision is None:
        reasons = ["unknown-data-class"] if resolved_class is None else []
        reasons.append(packet_error or "bad-decision")
        spoken = (
            "Held. The data class is unknown, so I did not start anything."
            if resolved_class is None
            else "Meta harness did not answer. I did not start anything."
        )
        return {
            "ok": False,
            "started": False,
            "spoken": spoken,
            "job": resolved_job,
            "primary": "",
            "refuse": True,
            "reasons": reasons,
            "utterance": text,
            "data_class": resolved_class or "unknown",
            "packet": True,
        }

    reasons = [str(item) for item in decision.get("reasons") or []]
    if job_coerced:
        reasons = ["job-coerced-to-dispatch", *reasons]
    would_spawn = bool(decision.get("spawn"))
    if resolved_class is None:
        return {
            "ok": False,
            "started": False,
            "spoken": "Held. The data class is unknown, so I did not start anything.",
            "job": str(decision.get("job") or resolved_job),
            "primary": "",
            "refuse": True,
            "reasons": ["unknown-data-class", *reasons],
            "next": "",
            "data_class": "unknown",
            "utterance": text,
            "table_would_spawn": would_spawn,
            "packet": True,
        }

    primary = str(decision.get("primary") or "")
    refuse = bool(decision.get("refuse"))
    return {
        "ok": not refuse and bool(primary),
        "started": False,
        "spoken": _spoken(resolved_job, primary, refuse, reasons),
        "job": str(decision.get("job") or resolved_job),
        "primary": primary,
        "refuse": refuse,
        "reasons": reasons,
        "next": str(decision.get("next") or ""),
        "data_class": resolved_class,
        "utterance": text,
        "table_would_spawn": would_spawn,
        "packet": True,
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
