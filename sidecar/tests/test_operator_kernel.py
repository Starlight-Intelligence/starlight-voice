"""Contract: every engine gets the same tools, with the same authority, from one registry."""

import inspect

from starlight_voice import operator_kernel as kernel
from starlight_voice import x_voice


def test_realtime_and_chat_projections_expose_the_same_tools() -> None:
    realtime = {t["name"]: t["parameters"] for t in kernel.realtime_schemas()}
    chat = {t["function"]["name"]: t["function"]["parameters"] for t in kernel.chat_schemas()}
    assert realtime == chat
    assert set(realtime) == kernel.NAMES
    assert len(kernel.TOOLS) == len(kernel.NAMES)


def test_ara_session_advertises_exactly_the_kernel_tools() -> None:
    session = x_voice.session_update(server_tools=False)["session"]
    functions = {t["name"] for t in session["tools"] if t.get("type") == "function"}
    assert functions == kernel.NAMES
    assert x_voice.CLIENT_TOOLS == kernel.NAMES


def test_ara_dispatches_every_kernel_tool() -> None:
    source = inspect.getsource(x_voice._finish_tools)
    missing = [name for name in kernel.NAMES if f'name == "{name}"' not in source]
    assert missing == []


def test_only_the_spoken_go_tools_carry_consent_and_none_writes_an_approval() -> None:
    consent = {t.name for t in kernel.TOOLS if t.authority == "consent"}
    assert consent == {"confirm_handoff", "triage_decide"}
    for tool in kernel.TOOLS:
        assert "approve" not in tool.name
        assert "ack" not in tool.name.split("_")


def test_read_only_projection_cannot_propose_or_consent() -> None:
    read = frozenset({"read"})
    names = {t["name"] for t in kernel.realtime_schemas(authority=read)}
    assert names == {"estate_lookup", "handoff_status", "recall", "estate_status", "triage_next"}
    assert {t["function"]["name"] for t in kernel.chat_schemas(authority=read)} == names


def test_every_schema_is_a_closed_object_with_its_required_fields_defined() -> None:
    for tool in kernel.TOOLS:
        params = tool.parameters
        assert params["type"] == "object"
        assert set(params.get("required", [])) <= set(params["properties"])
        assert tool.description.strip()
