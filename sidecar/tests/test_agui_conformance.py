"""AG-UI conformance: our constants must be a subset of the published spec.

Source: https://docs.ag-ui.com/concepts/events, read 2026-11-12.
This is not a full schema validator - it is a drift guard. If someone renames a
constant or invents an event type, this fails before a client has to guess what
the unknown event meant.

When the spec adds an event, add it here in the same commit that uses it.
"""

from __future__ import annotations

import unittest

from starlight_voice import agui_events as agui

SPEC_SOURCE = "https://docs.ag-ui.com/concepts/events"
SPEC_DATE = "2026-11-12"

# Every event type documented on that page (deprecated THINKING_* excluded).
DOCUMENTED = {
    # lifecycle
    "RUN_STARTED",
    "RUN_FINISHED",
    "RUN_ERROR",
    "STEP_STARTED",
    "STEP_FINISHED",
    # text
    "TEXT_MESSAGE_START",
    "TEXT_MESSAGE_CONTENT",
    "TEXT_MESSAGE_END",
    "TEXT_MESSAGE_CHUNK",
    # tools
    "TOOL_CALL_START",
    "TOOL_CALL_ARGS",
    "TOOL_CALL_END",
    "TOOL_CALL_RESULT",
    "TOOL_CALL_CHUNK",
    # state
    "STATE_SNAPSHOT",
    "STATE_DELTA",
    "MESSAGES_SNAPSHOT",
    # activity
    "ACTIVITY_SNAPSHOT",
    "ACTIVITY_DELTA",
    # special
    "RAW",
    "CUSTOM",
    # reasoning
    "REASONING_START",
    "REASONING_MESSAGE_START",
    "REASONING_MESSAGE_CONTENT",
    "REASONING_MESSAGE_END",
    "REASONING_END",
    "REASONING_ENCRYPTED_VALUE",
    # subagents
    "SUBAGENT_STARTED",
    "SUBAGENT_FINISHED",
    "SUBAGENT_ERROR",
}


def our_constants() -> dict[str, str]:
    return {
        name: value
        for name, value in vars(agui.EventType).items()
        if not name.startswith("_") and isinstance(value, str)
    }


class ConformanceTests(unittest.TestCase):
    def test_every_constant_is_a_documented_event_name(self):
        for name, value in our_constants().items():
            self.assertEqual(name, value, f"{name} must equal its wire value")
            self.assertIn(
                value,
                DOCUMENTED,
                f"{value} is not in the AG-UI spec as of {SPEC_DATE} ({SPEC_SOURCE})",
            )

    def test_the_run_boundary_names_are_the_documented_ones(self):
        self.assertEqual(agui.EventType.RUN_STARTED, "RUN_STARTED")
        self.assertEqual(agui.EventType.RUN_FINISHED, "RUN_FINISHED")
        self.assertEqual(agui.EventType.RUN_ERROR, "RUN_ERROR")

    def test_we_do_not_ship_deprecated_thinking_events(self):
        values = set(our_constants().values())
        self.assertFalse({v for v in values if v.startswith("THINKING_")})

    def test_schema_version_is_declared(self):
        self.assertTrue(agui.SCHEMA_VERSION.startswith("ag-ui."))

    def test_run_started_carries_thread_and_run_ids(self):
        event = agui.run_started("t1", "r1")
        self.assertEqual(event["threadId"], "t1")
        self.assertEqual(event["runId"], "r1")

    def test_text_message_events_carry_the_required_fields(self):
        start, content, end = agui.text_message("m1", "assistant", "hi")
        self.assertEqual(start["messageId"], "m1")
        self.assertIn(start["role"], {"developer", "system", "assistant", "user", "tool"})
        self.assertEqual(content["delta"], "hi")
        self.assertEqual(end["messageId"], "m1")

    def test_run_finished_carries_the_success_outcome(self):
        event = agui.run_finished("r1")
        self.assertEqual(event["outcome"]["type"], "success")
        self.assertEqual(event["runId"], "r1")


if __name__ == "__main__":
    unittest.main()
