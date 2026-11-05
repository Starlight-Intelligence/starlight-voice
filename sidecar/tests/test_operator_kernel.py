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


def test_ara_handles_propose_and_consent_itself_and_reads_through_the_kernel() -> None:
    source = inspect.getsource(x_voice._finish_tools)
    gated = {t.name for t in kernel.TOOLS if t.authority != "read"}
    assert [name for name in gated if f'name == "{name}"' not in source] == []
    reads = {t.name for t in kernel.TOOLS if t.authority == "read"}
    assert [name for name in reads if f'name == "{name}"' in source] == []
    assert "operator_kernel.run_read(name, arguments)" in source


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


def test_every_read_tool_has_one_shared_handler() -> None:
    reads = {t.name for t in kernel.TOOLS if t.authority == "read"}
    assert reads == set(kernel._READ_HANDLERS)


def test_run_read_refuses_propose_consent_and_unknown_tools() -> None:
    import asyncio

    for name in ("meta_harness", "confirm_handoff", "triage_decide", "approve", ""):
        result = asyncio.run(kernel.run_read(name, {}))
        assert result["ok"] is False
        assert result["started"] is False


def test_run_read_calls_the_same_handler_ara_uses(monkeypatch) -> None:
    import asyncio

    seen: list[str] = []
    monkeypatch.setattr("starlight_voice.tool_plane.recall", lambda q: seen.append(q) or {"ok": True, "spoken": "found"})
    result = asyncio.run(kernel.run_read("recall", {"query": "voice plan"}))
    assert result == {"ok": True, "spoken": "found"}
    assert seen == ["voice plan"]
