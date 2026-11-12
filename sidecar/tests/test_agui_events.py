"""Contract tests for the AG-UI emitter.

These test the stream shape, not a transport. If a transport is added later,
these stay valid because they only assert the wire dictionaries.
"""

from __future__ import annotations

import pytest

from starlight_voice import agui_events as agui

REPLY = {
    "reply": "Queen observation: dispatcher idle. Found 0 inbox artifacts.",
    "detectedLanguage": "en",
    "proposedTask": None,
    "mode": "bounded-status-router",
    "grounding": {"observedAt": "2026-11-12T00:00:00Z", "queuedTasks": 0, "activeLanes": 0},
}


def test_reply_stream_is_valid_and_ordered():
    events = agui.reply_to_events(REPLY, thread_id="t1", run_id="r1")
    assert agui.validate_stream(events) == []
    assert events[0]["type"] == agui.EventType.RUN_STARTED
    assert events[-1]["type"] == agui.EventType.RUN_FINISHED

    kinds = [e["type"] for e in events]
    assert kinds.index(agui.EventType.STATE_SNAPSHOT) < kinds.index(agui.EventType.TEXT_MESSAGE_START)


def test_state_snapshot_carries_grounding_not_the_text():
    events = agui.reply_to_events(REPLY, thread_id="t1", run_id="r1")
    snapshot = next(e for e in events if e["type"] == agui.EventType.STATE_SNAPSHOT)["snapshot"]
    assert snapshot["mode"] == "bounded-status-router"
    assert snapshot["grounding"]["queuedTasks"] == 0
    assert REPLY["reply"] not in snapshot.values()


def test_text_is_streamed_as_one_message():
    events = agui.reply_to_events(REPLY, thread_id="t1", run_id="r1", message_id="m1")
    contents = [e for e in events if e["type"] == agui.EventType.TEXT_MESSAGE_CONTENT]
    assert "".join(e["delta"] for e in contents) == REPLY["reply"]
    assert all(e["messageId"] == "m1" for e in contents)


def test_proposed_task_becomes_a_custom_event_after_the_text():
    reply = dict(REPLY, proposedTask={"title": "Ship the thing", "prompt": "ship it"})
    events = agui.reply_to_events(reply, thread_id="t1", run_id="r1")
    assert agui.validate_stream(events) == []
    kinds = [e["type"] for e in events]
    custom_index = kinds.index(agui.EventType.CUSTOM)
    assert custom_index > kinds.index(agui.EventType.TEXT_MESSAGE_END)
    custom = events[custom_index]
    assert custom["name"] == "proposed_task"
    assert custom["value"]["title"] == "Ship the thing"


def test_chunking_preserves_content():
    events = agui.text_message("m1", "assistant", "abcdefghij", chunk_size=3)
    assert [e["delta"] for e in events if e["type"] == agui.EventType.TEXT_MESSAGE_CONTENT] == [
        "abc",
        "def",
        "ghi",
        "j",
    ]


def test_empty_text_still_produces_a_balanced_message():
    events = agui.text_message("m1", "assistant", "")
    assert [e["type"] for e in events] == [
        agui.EventType.TEXT_MESSAGE_START,
        agui.EventType.TEXT_MESSAGE_END,
    ]


def test_rejects_unknown_role():
    with pytest.raises(ValueError):
        agui.text_message("m1", "robot", "hi")


def test_rejects_non_string_reply():
    with pytest.raises(ValueError):
        agui.reply_to_events({"reply": 42}, thread_id="t1", run_id="r1")


def test_validator_catches_missing_run_started():
    events = agui.text_message("m1", "assistant", "hi")
    problems = agui.validate_stream(events)
    assert any("RUN_STARTED" in p for p in problems)


def test_validator_catches_unbalanced_message():
    events = [
        agui.run_started("t1", "r1"),
        {"type": agui.EventType.TEXT_MESSAGE_START, "messageId": "m1", "role": "assistant"},
        {"type": agui.EventType.TEXT_MESSAGE_CONTENT, "messageId": "m1", "delta": "hi"},
        agui.run_finished("r1"),
    ]
    problems = agui.validate_stream(events)
    assert any("open id: m1" in p for p in problems)


def test_validator_catches_events_after_terminal():
    events = agui.reply_to_events(REPLY, thread_id="t1", run_id="r1")
    events.append(agui.custom("late", {}))
    assert any("after the terminal" in p for p in agui.validate_stream(events))


def test_validator_catches_empty_delta():
    events = [
        agui.run_started("t1", "r1"),
        {"type": agui.EventType.TEXT_MESSAGE_START, "messageId": "m1", "role": "assistant"},
        {"type": agui.EventType.TEXT_MESSAGE_CONTENT, "messageId": "m1", "delta": ""},
        {"type": agui.EventType.TEXT_MESSAGE_END, "messageId": "m1"},
        agui.run_finished("r1"),
    ]
    assert any("empty delta" in p for p in agui.validate_stream(events))


def test_error_run_is_a_valid_terminal():
    events = [agui.run_started("t1", "r1"), agui.run_error("r1", "provider refused", code="E_PROVIDER")]
    assert agui.validate_stream(events) == []
    assert events[-1]["type"] == agui.EventType.RUN_ERROR


def test_interrupt_outcome_shape():
    event = agui.interrupt_outcome("int_1", "approve browser submit", {"url": "example.test"})
    assert event["type"] == agui.EventType.RUN_FINISHED
    assert event["outcome"]["type"] == "interrupt"
    assert event["outcome"]["interrupts"][0]["id"] == "int_1"


def test_empty_stream_is_invalid():
    assert agui.validate_stream([]) == ["empty stream"]
