"""Scan repos for open loops, rank them, emit a morning brief.

Reuses the exact audit shape that surfaced Frank's real portability gaps (uncommitted /
unpushed / diverged state). Deterministic scoring; one optional cheap OpenRouter call phrases
the spoken summary. No network needed for the scan itself — it stays testable headless.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class RepoFinding:
    repo: str
    branch: str
    uncommitted: int
    ahead: int
    behind: int
    no_remote: bool

    @property
    def score(self) -> int:
        """Higher = more attention. Unpushed/diverged work outranks mere dirt; no-remote is risk."""
        s = 0
        s += 5 if self.no_remote else 0          # work that can't survive a disk failure
        s += 3 * self.ahead                       # local commits not on origin
        s += 2 * self.behind                      # drift from origin
        s += min(self.uncommitted, 10)            # dirty tree (capped)
        return s

    def to_dict(self) -> dict[str, object]:
        return {
            "repo": self.repo,
            "branch": self.branch,
            "uncommitted": self.uncommitted,
            "ahead": self.ahead,
            "behind": self.behind,
            "no_remote": self.no_remote,
            "score": self.score,
        }


def _git(repo: Path, *args: str) -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True, text=True, timeout=10, check=False,
        )
        return (out.stdout or "").strip()
    except Exception:
        return ""


def scan_repos(paths: list[Path]) -> list[RepoFinding]:
    findings: list[RepoFinding] = []
    for path in paths:
        if not (path / ".git").exists():
            continue
        branch = _git(path, "rev-parse", "--abbrev-ref", "HEAD") or "?"
        uncommitted = len([ln for ln in _git(path, "status", "--porcelain").splitlines() if ln.strip()])
        has_remote = bool(_git(path, "remote"))
        ahead = behind = 0
        if has_remote:
            counts = _git(path, "rev-list", "--left-right", "--count", "HEAD...@{u}")
            parts = counts.split()
            if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                ahead, behind = int(parts[0]), int(parts[1])
        findings.append(RepoFinding(
            repo=path.name, branch=branch, uncommitted=uncommitted,
            ahead=ahead, behind=behind, no_remote=not has_remote,
        ))
    return findings


def score_findings(findings: list[RepoFinding]) -> list[RepoFinding]:
    """Rank by score desc; drop quiet repos (nothing actionable)."""
    actionable = [f for f in findings if f.score > 0]
    return sorted(actionable, key=lambda f: f.score, reverse=True)


@dataclass
class Brief:
    date: str
    items: list[RepoFinding] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {"date": self.date, "count": len(self.items), "items": [f.to_dict() for f in self.items]}

    def headline(self, top: int = 3) -> str:
        """Deterministic spoken fallback (used if no OpenRouter synthesis)."""
        if not self.items:
            return "All repos clean. Nothing pending."
        bits = []
        for f in self.items[:top]:
            parts = []
            if f.no_remote:
                parts.append("no remote")
            if f.ahead:
                parts.append(f"{f.ahead} unpushed")
            if f.behind:
                parts.append(f"{f.behind} behind")
            if f.uncommitted:
                parts.append(f"{f.uncommitted} uncommitted")
            bits.append(f"{f.repo}: {', '.join(parts)}")
        return "Top open loops - " + "; ".join(bits) + "."


def build_brief(paths: list[Path], date: str) -> Brief:
    return Brief(date=date, items=score_findings(scan_repos(paths)))


def aggregate(brief: Brief) -> dict[str, int]:
    """Cross-repo totals — the at-a-glance picture beneath the per-repo ranking."""
    items = brief.items
    return {
        "repos_flagged": len(items),
        "total_unpushed": sum(f.ahead for f in items),
        "total_behind": sum(f.behind for f in items),
        "total_uncommitted": sum(f.uncommitted for f in items),
        "no_remote": sum(1 for f in items if f.no_remote),
    }


def recommend_next_action(brief: Brief) -> str:
    """The single highest-leverage next move, spoken. Items are pre-sorted by score desc."""
    if not brief.items:
        return "Nothing pending — every repo is clean."
    top = brief.items[0]
    why = []
    if top.no_remote:
        why.append("no remote, so the work can't survive a disk failure")
    if top.ahead:
        why.append(f"{top.ahead} unpushed")
    if top.behind:
        why.append(f"{top.behind} behind origin")
    if top.uncommitted:
        why.append(f"{top.uncommitted} uncommitted")
    reason = "; ".join(why) if why else "it's the highest-priority open loop"
    return f"Start with {top.repo}: {reason}."


def write_brief(brief: Brief, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"brief-{brief.date}.json"
    path.write_text(json.dumps(brief.to_dict(), indent=2), encoding="utf-8")
    return path


def synthesize_spoken(brief: Brief, *, model: str = "google/gemini-3-flash") -> str:
    """Optional: one cheap OpenRouter call to phrase the brief naturally. Falls back to headline()."""
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key or not brief.items:
        return brief.headline()
    try:
        import httpx

        prompt = (
            "Phrase this repo status as a <=2 sentence spoken morning brief for Frank, action-first, "
            "no preamble:\n" + json.dumps(brief.to_dict())
        )
        resp = httpx.post(
            f"{os.environ.get('OPENROUTER_BASE_URL', 'https://openrouter.ai/api/v1')}/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": model, "messages": [{"role": "user", "content": prompt}], "max_tokens": 120},
            timeout=15,
        )
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception:
        return brief.headline()
