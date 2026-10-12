"""Preview a coding handoff and record its outcome without launching a harness.

Legacy fleet choices and keyword tiers are routing hints, not admission authority.
Managed dispatch is unavailable until the accepted controller is connected with
verified ownership, resource, cost and approval policy. This module creates no queue.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

import ulid

from .fleet import select_agent
from .ledger import record_run, resolve_relevant_files

# Retained preview risk hints: A read-only wording, B mutation/unknown,
# C substrate, D always-ask. No keyword or model classification proves safety
# or grants execution authority. Even A requires controller admission.
_ALWAYS_ASK = (
    "rotate", "force push", "force-push", "reset --hard", "drop table", "drop database",
    "wipe prod", "wipe production", "nuke", "send email", "email blast", "stripe", "charge",
    "refund", "payment", "business/", "papa/", "delete database", "factory reset",
)
_SUBSTRATE = ("substrate", "sip protocol", "sovereignty", "schema migration", "breaking change")
_READONLY_LEAD = (
    "read", "show", "list", "summarize", "explain", "search", "find", "check", "status",
    "diff", "log", "what", "analyze", "review", "describe", "print", "tell me", "how ",
)
_MUTATION = (
    "write", "delete", "remove", "push", "merge", "deploy", "publish", "create", "modify",
    "edit", "run", "install", "rm ", "drop", "reset", "rename", "move", "commit", "refactor",
    "fix", "update", "add", "build", "send", "rotate", "kill", "stop",
)


def approval_tier(task: str) -> str:
    t = " ".join(task.lower().split())
    if any(k in t for k in _ALWAYS_ASK):
        return "D"
    if any(k in t for k in _SUBSTRATE):
        return "C"
    # A describes wording only; it never permits execution.
    leads_readonly = any(t == v or t.startswith(v + " ") for v in _READONLY_LEAD)
    if leads_readonly and not any(m in t for m in _MUTATION):
        return "A"
    return "B"


@dataclass(frozen=True)
class HandoffPacket:
    packet_id: str
    created_at: str
    source: str
    intent_class: str
    target_system: str        # repo name (from agent-sessions.json) or fleet agent name
    target_cli: str           # legacy suggested CLI, not a verified executable target
    target_model: str
    task: str
    complexity: int
    approval_required: bool
    approval_tier: str
    spoken_update_for_frank: str
    relevant_files: list[str] = field(default_factory=list)
    target_cwd: str = ""       # suggested repo path, not a checked write lane

    def to_dict(self) -> dict[str, object]:
        return {
            "packet_version": 1,
            "packet_id": self.packet_id,
            "created_at": self.created_at,
            "source": self.source,
            "classification": {"intent_class": self.intent_class},
            "target_system": self.target_system,
            "target": {"cli": self.target_cli, "model": self.target_model, "cwd": self.target_cwd},
            "task": self.task,
            "complexity": self.complexity,
            "context": {"relevant_files": self.relevant_files},
            "approval": {"required": self.approval_required, "tier": self.approval_tier},
            "execution": {"mode": "preview", "admitted": False},
            "spoken_update_for_frank": self.spoken_update_for_frank,
        }


def build_handoff_packet(
    task: str, *, source: str = "voice", now: datetime | None = None, classifier=None
) -> HandoffPacket:
    # ONE classification (LLM structured-output when a classifier is injected, else keyword
    # fail-closed). Lazy import breaks the classify<->dispatch cycle.
    from .classify import classify
    from .sessions import agent_binary, resolve_repo

    c = classify(task, llm_call=classifier)
    score, tier = c.complexity, c.approval_tier
    agent = select_agent(score)  # cost-band fallback (model + binary by complexity)

    # Retain the historical per-repo suggestion without treating it as a lease
    # or verifying that its configured CLI, model and directory remain usable.
    repo = resolve_repo(task)
    if repo:
        target_system = repo["name"]
        target_cli = agent_binary(repo.get("agent", "")) or agent.cli
        target_cwd = repo.get("path", "")
        relevant_files = [repo["path"]] if repo.get("path") else resolve_relevant_files(task)
    else:
        target_system, target_cli, target_cwd = agent.name, agent.cli, ""
        relevant_files = resolve_relevant_files(task)

    stamp = (now or datetime.now(UTC)).isoformat()
    if tier == "D":
        spoken = "Blocked: this is an always-ask action. No harness has started."
    else:
        spoken = "Handoff preview prepared. No harness has started; managed dispatch is unavailable."
    return HandoffPacket(
        packet_id=str(ulid.new()),
        created_at=stamp,
        source=source,
        intent_class="cli-agent-task",
        target_system=target_system,
        target_cli=target_cli,
        target_model=agent.model,
        task=task,
        complexity=score,
        approval_required=True,
        approval_tier=tier,
        spoken_update_for_frank=spoken,
        relevant_files=relevant_files,
        target_cwd=target_cwd,
    )


class Dispatcher:
    """Build packet previews; live requests remain unavailable for every tier."""

    def __init__(self, *, live: bool = False) -> None:
        self.live = live

    def dispatch(self, task: str, *, source: str = "voice") -> dict[str, object]:
        packet = build_handoff_packet(task, source=source)
        pd = packet.to_dict()

        # Single-exit so EVERY outcome is recorded to the append-only audit ledger.
        if packet.approval_tier == "D":
            # Tier D = CLAUDE.md ALWAYS-ASK — hard stop, never auto-runs even live.
            status, note = "hard-blocked", "Always-ask action. No work started; this path cannot grant approval."
        elif not self.live:
            status, note = "dry-run", "Packet preview only. No harness started or work queued."
        else:
            status, note = "unavailable", "Managed dispatch is unavailable. Controller admission is required; no harness started or work queued."

        record_run(pd, status)
        result: dict[str, object] = {"packet": pd, "status": status}
        if note:
            result["note"] = note
        return result
