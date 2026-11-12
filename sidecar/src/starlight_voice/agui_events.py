"""AG-UI event emission for the Starlight Voice runtime.

AG-UI is the agent-to-UI protocol (https://docs.ag-ui.com). It is event-based:
a run begins with RUN_STARTED, streams TEXT_MESSAGE_* / TOOL_CALL_* / STATE_*
events, and ends with RUN_FINISHED or RUN_ERROR.

Why this module is dependency-free: the surface can be proven before the SDK,
the model, or a provider key exists. The dictionaries produced here are the same
wire objects an SDK transport serialises, so swapping in the official SDK later
is a transport change, not a rewrite.

Deliberate omissions:
  * no audio events yet - voice is a transport, the event contract is not
  * no interrupt emission helper yet - side effects must not ship without one,
    so `interrupt_outcome()` exists but is not used by `reply_to_events()`
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping
from typing import Any

SCHEMA_VERSION = "ag-ui.events.v1"


class EventType:
    RUN_STARTED = "RUN_STARTED"
    RUN_FINISHED = "RUN_FINISHED"
    RUN_ERROR = "RUN_ERROR"

    STEP_STARTED = "STEP_STARTED"
    STEP_FINISHED = "STEP_FINISHED"

    TEXT_MESSAGE_START = "TEXT_MESSAGE_START"
    TEXT_MESSAGE_CONTENT = "TEXT_MESSAGE_CONTENT"
    TEXT_MESSAGE_END = "TEXT_MESSAGE_END"

    TOOL_CALL_START = "TOOL_CALL_START"
    TOOL_CALL_ARGS = "TOOL_CALL_ARGS"
    TOOL_CALL_END = "TOOL_CALL_END"
    TOOL_CALL_RESULT = "TOOL_CALL_RESULT"

    STATE_SNAPSHOT = "STATE_SNAPSHOT"
    STATE_DELTA = "STATE_DELTA"
    MESSAGES_SNAPSHOT = "MESSAGES_SNAPSHOT"

    CUSTOM = "CUSTOM"


# Event types that open and close a message or tool-call stream.
_OPENERS = {EventType.TEXT_MESSAGE_START, EventType.TOOL_CALL_START}
_CLOSERS = {EventType.TEXT_MESSAGE_END, EventType.TOOL_CALL_END}


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _event(event_type: str, **fields: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"type": event_type}
    payload.update({k: v for k, v in fields.items() if v is not None})
    return payload


def run_started(thread_id: str, run_id: str, input_payload: Any | None = None) -> dict[str, Any]:
    return _event(EventType.RUN_STARTED, threadId=thread_id, runId=run_id, input=input_payload)


def run_finished(run_id: str, result: Any | None = None) -> dict[str, Any]:
    return _event(EventType.RUN_FINISHED, runId=run_id, outcome={"type": "success"}, result=result)


def run_error(run_id: str | None, message: str, code: str | None = None) -> dict[str, Any]:
    return _event(EventType.RUN_ERROR, runId=run_id, message=message, code=code)


def interrupt_outcome(interrupt_id: str, reason: str, payload: Any | None = None) -> dict[str, Any]:
    """A run that paused for a human. Nothing in this module emits it yet.

    Every side effect (send, deploy, merge, spend, delete, browser submit) must
    terminate its run with this instead of RUN_FINISHED.
    """
    return _event(
        EventType.RUN_FINISHED,
        runId=None,
        outcome={
            "type": "interrupt",
            "interrupts": [{"id": interrupt_id, "reason": reason, "payload": payload}],
        },
    )


def text_message(message_id: str, role: str, text: str, chunk_size: int | None = None) -> list[dict[str, Any]]:
    """Start -> content -> end. Chunks exist so a UI can render streaming."""
    if role not in {"developer", "system", "assistant", "user", "tool"}:
        raise ValueError(f"unsupported role: {role}")

    events = [_event(EventType.TEXT_MESSAGE_START, messageId=message_id, role=role)]
    if chunk_size and chunk_size > 0:
        pieces = [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)]
    else:
        pieces = [text] if text else []
    for piece in pieces:
        if piece:
            events.append(_event(EventType.TEXT_MESSAGE_CONTENT, messageId=message_id, delta=piece))
    events.append(_event(EventType.TEXT_MESSAGE_END, messageId=message_id))
    return events


def state_snapshot(state: Mapping[str, Any]) -> dict[str, Any]:
    return _event(EventType.STATE_SNAPSHOT, snapshot=dict(state))


def custom(name: str, value: Any) -> dict[str, Any]:
    return _event(EventType.CUSTOM, name=name, value=value)


def reply_to_events(
    reply: Mapping[str, Any],
    thread_id: str,
    run_id: str,
    message_id: str | None = None,
) -> list[dict[str, Any]]:
    """Map a bounded-router reply to an AG-UI stream.

    The input shape is the one the observatory already produces:
      {reply, detectedLanguage, proposedTask, mode, grounding}

    Ordering matters: the state snapshot precedes the message so a client can
    render provenance (mode, grounding) alongside the words, and a proposed task
    arrives as a CUSTOM event after the text it refers to.
    """
    text = reply.get("reply")
    if not isinstance(text, str):
        raise ValueError("reply['reply'] must be a string")

    message_id = message_id or new_id("msg")
    events: list[dict[str, Any]] = [
        run_started(thread_id=thread_id, run_id=run_id),
        state_snapshot(
            {
                "mode": reply.get("mode"),
                "detectedLanguage": reply.get("detectedLanguage"),
                "grounding": reply.get("grounding"),
            }
        ),
        *text_message(message_id, "assistant", text),
    ]

    proposed = reply.get("proposedTask")
    if proposed:
        events.append(custom("proposed_task", proposed))

    events.append(run_finished(run_id))
    return events


def validate_stream(events: Iterable[Mapping[str, Any]]) -> list[str]:
    """Return a list of contract violations. Empty means the stream is valid.

    Checks only the rules this estate depends on: one run boundary, balanced
    message and tool-call streams, non-empty deltas, and nothing after a
    terminal event.
    """
    problems: list[str] = []
    events = list(events)
    if not events:
        return ["empty stream"]

    run_starts = [i for i, e in enumerate(events) if e.get("type") == EventType.RUN_STARTED]
    terminals = [
        i
        for i, e in enumerate(events)
        if e.get("type") in {EventType.RUN_FINISHED, EventType.RUN_ERROR}
    ]

    if len(run_starts) != 1:
        problems.append(f"expected exactly one RUN_STARTED, found {len(run_starts)}")
    elif run_starts[0] != 0:
        problems.append("RUN_STARTED must be the first event")

    if not terminals:
        problems.append("stream has no RUN_FINISHED or RUN_ERROR")
    else:
        last = terminals[-1]
        if last != len(events) - 1:
            problems.append("events found after the terminal event")
        if len(terminals) > 1:
            problems.append(f"more than one terminal event: {len(terminals)}")

    open_ids: dict[str, str] = {}
    for event in events:
        kind = event.get("type")
        if kind in _OPENERS:
            opener_id = event.get("messageId") or event.get("toolCallId")
            if not opener_id:
                problems.append(f"{kind} without an id")
            elif opener_id in open_ids:
                problems.append(f"id opened twice: {opener_id}")
            else:
                open_ids[opener_id] = kind
        elif kind in _CLOSERS:
            closer_id = event.get("messageId") or event.get("toolCallId")
            if closer_id not in open_ids:
                problems.append(f"{kind} closes an unopened id: {closer_id}")
            else:
                open_ids.pop(closer_id)
        elif kind == EventType.TEXT_MESSAGE_CONTENT:
            if not event.get("delta"):
                problems.append("TEXT_MESSAGE_CONTENT with an empty delta")
            if event.get("messageId") not in open_ids:
                problems.append(f"content for unopened message: {event.get('messageId')}")

    for leftover in open_ids:
        problems.append(f"stream ended with an open id: {leftover}")

    return problems
