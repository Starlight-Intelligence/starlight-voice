"""One tool registry for every voice engine.

The xAI realtime socket, a Pipecat cascade, or any chat-completions model all project their tool
list from TOOLS, so Ara and a fallback engine can never drift into different powers. Each tool
declares its authority. No tool lets the model write a human approval: confirm_handoff records the
spoken go only, and the ack stays on the tray, the CLI, or the same-origin dashboard POST.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .meta_harness import JOBS

Authority = Literal["read", "propose", "consent"]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, object]
    authority: Authority


def _query(description: str = "") -> dict[str, object]:
    prop: dict[str, object] = {"type": "string"}
    if description:
        prop["description"] = description
    return {"type": "object", "properties": {"query": prop}, "required": ["query"]}


_PROPOSAL = {
    "type": "object",
    "properties": {"proposal": {"type": "string", "description": "The proposal id meta_harness returned."}},
    "required": ["proposal"],
}

TOOLS: tuple[Tool, ...] = (
    Tool(
        "meta_harness",
        "Route one spoken work request through the Starlight meta harness. "
        "Returns the seat and, for public repo work, a proposal that needs Frank's spoken go "
        "and his approve press. Does not start the seat.",
        {
            "type": "object",
            "properties": {
                "utterance": {"type": "string", "description": "What Frank asked, verbatim."},
                "job": {"type": "string", "enum": list(JOBS)},
            },
            "required": ["utterance", "job"],
        },
        "propose",
    ),
    Tool(
        "estate_lookup",
        "Look up a phrase on this machine: the voice session, the live phase, "
        "and the Starlight desk files. Does not search the public web and does not start work.",
        _query(),
        "read",
    ),
    Tool(
        "confirm_handoff",
        "Record Frank's spoken go for one proposal. Call only after he says go for it. "
        "Queues a Queen card only if he has also pressed approve.",
        _PROPOSAL,
        "consent",
    ),
    Tool(
        "handoff_status",
        "Read where one proposal is: waiting, queued, running, reported, or failed.",
        _PROPOSAL,
        "read",
    ),
    Tool(
        "recall",
        "Search Frank's memory vault: decisions, plans, projects, people, and earlier work. "
        "Read-only. Use it before guessing what Frank meant or decided.",
        _query("What to recall, in a few words."),
        "read",
    ),
    Tool(
        "estate_status",
        "Read one live view of the estate: the work queue or the machine's health. Read-only.",
        {
            "type": "object",
            "properties": {"kind": {"type": "string", "enum": ["queue", "machine"]}},
            "required": ["kind"],
        },
        "read",
    ),
    Tool(
        "triage_next",
        "Read the next agent pull requests that need Frank, from the morning triage queue. Read-only.",
        {
            "type": "object",
            "properties": {"count": {"type": "integer", "description": "How many to read, 1 to 5."}},
            "required": [],
        },
        "read",
    ),
    Tool(
        "triage_decide",
        "Read back one triage decision for Frank to answer. Records nothing: his own spoken yes, "
        "heard directly by the listener, records it.",
        {
            "type": "object",
            "properties": {
                "ref": {"type": "string", "description": "The item number from the queue, digits only."},
                "action": {"type": "string", "enum": ["keep", "close", "review", "handoff"]},
            },
            "required": ["ref", "action"],
        },
        "consent",
    ),
)

NAMES = frozenset(tool.name for tool in TOOLS)


def by_name(name: str) -> Tool | None:
    return next((tool for tool in TOOLS if tool.name == name), None)


def realtime_schemas(*, authority: frozenset[Authority] | None = None) -> list[dict[str, object]]:
    """Flat function tools for a realtime socket (xAI Grok Voice, OpenAI Realtime)."""

    return [
        {"type": "function", "name": t.name, "description": t.description, "parameters": t.parameters}
        for t in TOOLS
        if authority is None or t.authority in authority
    ]


def chat_schemas(*, authority: frozenset[Authority] | None = None) -> list[dict[str, object]]:
    """Chat-completions function tools for a cascade engine (Pipecat LLM service, OpenRouter, Groq)."""

    return [
        {"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}}
        for t in TOOLS
        if authority is None or t.authority in authority
    ]
