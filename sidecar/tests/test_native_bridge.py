import asyncio
import json
import time

import httpx
import pytest

from starlight_voice.native_bridge import BridgeError, NativeBridge, mint_secret


def run(coro):
    return asyncio.run(coro)


async def fake_mint(provider, voice):
    return {"value": "ephemeral-only", "expires_at": time.time() + 60}


@pytest.fixture
def bridge():
    return NativeBridge(mint=fake_mint)


def start(bridge):
    return run(bridge.start({"provider": "grok", "voice": "ara"}))


def call(session, **changes):
    return {"session_id": session["session_id"], "call_id": "call-1", "name": "dispatch_task",
            "arguments": {"query": "Implement a change"}, **changes}


def test_fixed_session_policy_and_missing_keys(bridge, monkeypatch):
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    assert bridge.capabilities()["providers"][0]["configured"] is False
    assert bridge.capabilities()["provider_budget_enforced"] is False
    for payload in [
        {"provider": "grok", "voice": "ara", "instructions": "Override policy"},
        {"provider": "unknown", "voice": "ara"}, {"provider": [], "voice": "ara"},
        {"provider": "grok", "voice": "other"},
    ]:
        with pytest.raises(BridgeError):
            run(bridge.start(payload))
    session = start(bridge)
    assert session["model"] == "grok-voice-think-fast-2.0"
    assert session["max_session_seconds"] == 300
    with pytest.raises(BridgeError, match="active"):
        start(bridge)


def test_authority_and_blocked_writes_are_honest(bridge):
    session = start(bridge)
    with pytest.raises(BridgeError) as error:
        run(bridge.tool(call(session), "wrong"))
    assert error.value.status == 401
    result = run(bridge.tool(call(session), session["capability"]))
    assert result == {"status": "blocked", "reason": "Managed authenticated intake is not connected", "queued": False, "approved": False}
    assert run(bridge.tool(call(session), session["capability"])) == result
    assert bridge.session.remaining_calls == 19


@pytest.mark.parametrize("phase", ["completed", "pending", "cancelled"])
@pytest.mark.parametrize("change", ["query", "tool"])
def test_tool_call_identity_cannot_replay_a_different_request(bridge, monkeypatch, phase, change):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        reads = []

        async def read(name, arguments):
            reads.append((name, arguments))
            entered.set()
            if phase != "completed":
                await release.wait()
            return {"status": "ok", "reference": "First approved project summary"}

        monkeypatch.setattr(bridge, "_read", read)
        session = await bridge.start({"provider": "grok", "voice": "ara"})
        first = call(session, name="project_context", arguments={"query": "First project"})
        changed = {**first, "arguments": {"query": "Other project"}} if change == "query" else {**first, "name": "request_approval"}
        task = asyncio.create_task(bridge.tool(first, session["capability"]))
        try:
            await entered.wait()
            if phase == "completed":
                await task
            elif phase == "cancelled":
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            with pytest.raises(BridgeError) as error:
                await bridge.tool(changed, session["capability"])
            assert error.value.status == 409
            assert "First project" not in str(error.value) and "Other project" not in str(error.value)
            assert bridge.session.remaining_calls == 19
            assert reads == [("project_context", {"query": "First project"})]
        finally:
            release.set()
            if not task.done():
                await task

    run(scenario())


def test_identical_draft_retry_ignores_argument_key_order(bridge):
    session = start(bridge)
    arguments = {"objective": "Improve the existing voice conversation", "acceptance": "Verify interruption and microphone cleanup"}
    first = call(session, name="prepare_mission", arguments=arguments)
    result = run(bridge.tool(first, session["capability"]))
    retry = {**first, "arguments": dict(reversed(list(arguments.items())))}
    assert run(bridge.tool(retry, session["capability"])) == result
    assert bridge.session.remaining_calls == 19


@pytest.mark.parametrize("cancelled", [False, True])
def test_identical_read_retry_preserves_pending_or_cancelled_result(bridge, monkeypatch, cancelled):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        reads = []

        async def read(name, arguments):
            reads.append(name)
            entered.set()
            await release.wait()
            return {"status": "ok"}

        monkeypatch.setattr(bridge, "_read", read)
        session = await bridge.start({"provider": "grok", "voice": "ara"})
        payload = call(session, name="project_context", arguments={"query": "First project"})
        task = asyncio.create_task(bridge.tool(payload, session["capability"]))
        try:
            await entered.wait()
            if cancelled:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            retry = await bridge.tool(payload, session["capability"])
            assert retry == ({"status": "unavailable", "reason": "Scoped read was cancelled"} if cancelled
                             else {"status": "blocked", "reason": "This call is already in progress"})
            assert reads == ["project_context"] and bridge.session.remaining_calls == 19
        finally:
            release.set()
            if not task.done():
                await task

    run(scenario())


def test_node_health_uses_protocol_ping_and_never_scans_telemetry(bridge, monkeypatch):
    monkeypatch.setenv("STARLIGHT_TOOL_PLANE_TOKEN", "server-only-secret")
    seen = []

    class Reader:
        def __init__(self, endpoint, token):
            assert endpoint == "http://127.0.0.1:7317/mcp" and token == "server-only-secret"

        async def ping(self, *, expected_server):
            seen.append(expected_server)
            return {}

        async def call(self, *args, **kwargs):
            pytest.fail("A reachability probe must not invoke telemetry or any tool")

    monkeypatch.setattr("starlight_voice.native_bridge.LocalMcpReader", Reader)
    session = start(bridge)
    result = run(bridge.tool(call(session, name="node_health", arguments={}), session["capability"]))
    assert result == {"status": "ok", "shared_tool_plane": "responding", "scope": "mcp-ping-only", "hardware_health_verified": False, "audio_runtime_verified": False}
    assert seen == ["starlight-shared-tool-plane"]
    assert "server-only-secret" not in json.dumps(result)
    with pytest.raises(BridgeError):
        run(bridge.tool(call(session, name="vault_read"), session["capability"]))
    with pytest.raises(BridgeError):
        run(bridge.tool(call(session, arguments={"query": "x", "includePrivate": True}), session["capability"]))
    bridge.stop({"session_id": session["session_id"]}, session["capability"])
    with pytest.raises(BridgeError):
        run(bridge.tool(call(session), session["capability"]))


def test_tool_budget_and_expiry(bridge):
    session = start(bridge)
    for index in range(20):
        run(bridge.tool(call(session, call_id=str(index)), session["capability"]))
    with pytest.raises(BridgeError) as error:
        run(bridge.tool(call(session, call_id="overflow"), session["capability"]))
    assert error.value.status == 429
    bridge.session.expires = bridge.clock() - 1
    with pytest.raises(BridgeError) as error:
        run(bridge.tool(call(session), session["capability"]))
    assert error.value.status == 401


def test_projection_never_falls_back_to_private_vault(bridge, monkeypatch):
    monkeypatch.delenv("STARLIGHT_VOICE_PROJECTION_MCP_URL", raising=False)
    session = start(bridge)
    result = run(bridge.tool(call(session, name="sis_recall_projection", arguments={"query": "Project"}), session["capability"]))
    assert result["status"] == "unavailable"
    assert "projection" in result["reason"]


@pytest.mark.parametrize("name", ["sis_recall_projection", "project_context"])
@pytest.mark.parametrize("query", ["\U0001f9ed" * 257, " " * 600 + "Project", "\ud800"], ids=["emoji-over-limit", "padding-over-limit", "invalid-surrogate"])
def test_recall_rejects_queries_the_gateway_cannot_admit_without_spending_a_tool_call(bridge, monkeypatch, name, query):
    async def forbidden(*args):
        pytest.fail("Invalid gateway query reached a tool")

    monkeypatch.setattr(bridge, "_read", forbidden)
    session = start(bridge)
    with pytest.raises(BridgeError):
        run(bridge.tool(call(session, name=name, arguments={"query": query}), session["capability"]))
    assert bridge.session.remaining_calls == 20


@pytest.mark.parametrize("query", ["x" * 512, "\U0001f9ed" * 256], ids=["ascii-boundary", "emoji-boundary"])
def test_recall_accepts_the_actual_gateway_query_boundary(bridge, monkeypatch, query):
    async def read(name, arguments):
        assert name == "sis_recall_projection" and arguments == {"query": query}
        return {"status": "ok"}

    monkeypatch.setattr(bridge, "_read", read)
    session = start(bridge)
    assert run(bridge.tool(call(session, name="sis_recall_projection", arguments={"query": query}), session["capability"])) == {"status": "ok"}


def test_projection_delivers_watermark_as_reference_without_claiming_snapshot_binding(bridge, monkeypatch):
    monkeypatch.setenv("STARLIGHT_VOICE_PROJECTION_MCP_URL", "http://127.0.0.1:8787/mcp")
    monkeypatch.setenv("STARLIGHT_VOICE_PROJECTION_MCP_TOKEN", "private-bearer")
    watermark = {"projection_id": "a" * 64, "policy": "public-summaries-only", "observed_before_recall": True, "recall_snapshot_verified": False}

    class Reader:
        def __init__(self, endpoint, token):
            assert endpoint == "http://127.0.0.1:8787/mcp" and token == "private-bearer"

        async def call(self, name, args, *, projection):
            assert name == "memory_recall" and args == {"query": "Project", "limit": 4} and projection
            return {"content": [{"type": "text", "text": "Approved reference"}], "projection_watermark": watermark}

    monkeypatch.setattr("starlight_voice.native_bridge.LocalMcpReader", Reader)
    session = start(bridge)
    result = run(bridge.tool(call(session, name="project_context", arguments={"query": "Project"}), session["capability"]))
    assert result["projection_watermark"] == watermark
    assert result["instruction_authority"] is False and result["reference"] == "Approved reference"
    assert "private-bearer" not in json.dumps(result)


def test_prepare_mission_is_a_typed_ephemeral_draft_not_a_write(bridge, tmp_path):
    bridge.observation_path = tmp_path / "observations.jsonl"
    session = start(bridge)
    args = {"objective": "Improve the existing voice conversation", "acceptance": "Verify interruption and microphone cleanup"}
    payload = call(session, name="prepare_mission", arguments=args)
    with pytest.raises(BridgeError):
        run(bridge.tool(payload, "wrong"))
    result = run(bridge.tool(payload, session["capability"]))
    assert result["schema"] == "voice.mission-draft.v1" and result["status"] == "draft"
    assert result["queued"] is False and result["approved"] is False
    assert result["draft"]["objective"] == args["objective"]
    assert set(result["draft"]) == {"id", "objective", "acceptance"}
    assert run(bridge.tool(payload, session["capability"])) == result
    assert list(tmp_path.iterdir()) == []
    assert bridge.session.remaining_calls == 19
    bridge.observe({"session_id": session["session_id"], "observation": {
        "sequence": 1, "status": "completed", "interrupted": False, "token_counts": None,
        "response_created_ms": None, "first_scheduled_audio_ms": None, "response_done_ms": None,
    }}, session["capability"])
    stored = bridge.observation_path.read_text()
    assert args["objective"] not in stored and args["acceptance"] not in stored
    assert result["draft"]["id"] not in stored
    assert json.loads(stored)["session_tool_result_counts"] == {"prepare_mission:draft": 1}


@pytest.mark.parametrize("args", [
    {"objective": "too short", "acceptance": "Verify a useful actual outcome"},
    {"objective": "a" * 901, "acceptance": "Verify a useful actual outcome"},
    {"objective": "Improve the existing voice conversation", "acceptance": None},
    {"objective": "Improve the existing voice conversation", "acceptance": "Verify cleanup\0and recovery"},
    {"objective": "Improve the existing voice conversation", "acceptance": "Verify cleanup and recovery", "approved": True},
])
def test_mission_drafts_reject_unbounded_or_authority_fields(bridge, args):
    session = start(bridge)
    with pytest.raises(BridgeError):
        run(bridge.tool(call(session, name="prepare_mission", arguments=args), session["capability"]))


def test_mint_concurrency_and_failed_creation_release_reservation():
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        async def delayed(provider, voice):
            entered.set()
            await release.wait()
            raise BridgeError("unavailable", 502)

        bridge = NativeBridge(mint=delayed)
        first = asyncio.create_task(bridge.start({"provider": "grok", "voice": "ara"}))
        await entered.wait()
        with pytest.raises(BridgeError) as error:
            await bridge.start({"provider": "grok", "voice": "ara"})
        assert error.value.status == 429
        release.set()
        with pytest.raises(BridgeError):
            await first
        assert bridge.minting is False and bridge.session is None

    run(scenario())


@pytest.mark.parametrize("provider,voice,key,host", [
    ("grok", "orion", "XAI_API_KEY", "api.x.ai"), ("openai", "cedar", "OPENAI_API_KEY", "api.openai.com"),
])
def test_provider_request_shapes_and_no_project_key_response(provider, voice, key, host, monkeypatch):
    monkeypatch.setenv(key, "project-secret-do-not-disclose")

    def handler(request):
        assert request.url.host == host and request.url.path == "/v1/realtime/client_secrets"
        body = json.loads(request.content)
        if provider == "grok":
            assert body == {"expires_after": {"seconds": 60}}
        else:
            assert body["expires_after"] == {"anchor": "created_at", "seconds": 60}
            assert body["session"]["model"] == "gpt-realtime-2.1"
            assert body["session"]["audio"]["output"]["voice"] == voice
        return httpx.Response(200, json={"value": "ek_test", "expires_at": time.time() + 60, "other": "untrusted"})

    result = run(mint_secret(provider, voice, transport=httpx.MockTransport(handler)))
    assert set(result) == {"value", "expires_at"}
    assert "project-secret" not in json.dumps(result)


@pytest.mark.parametrize("response", [
    httpx.Response(302, headers={"Location": "https://other.example/leak"}),
    httpx.Response(401, text="private error containing project-key"),
    httpx.Response(200, json={"value": "bad\nkey", "expires_at": time.time() + 60}),
    httpx.Response(200, json={"value": "expired", "expires_at": 1}),
    httpx.Response(200, content=b"x" * 33000),
])
def test_provider_errors_are_redacted_and_redirects_rejected(response, monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "private-project-key")
    requests = []

    def handler(request):
        requests.append(request)
        return response

    with pytest.raises(BridgeError) as error:
        run(mint_secret("grok", "ara", transport=httpx.MockTransport(handler)))
    assert len(requests) == 1
    assert "private" not in str(error.value) and error.value.status == 502


@pytest.mark.parametrize("field", ["objective", "acceptance"])
@pytest.mark.parametrize("text", [
    *[f"Improve the existing voice {character} conversation" for character in
      ["\x01", "\x1b", "\x7f", "\x85", "\u202e", "\u2067", "\ud800"]],
    pytest.param("🚀" * 451, id="over-UTF16-limit"),
    pytest.param("\ufeff" + "x" * 14, id="browser-trim-minimum"),
])
def test_mission_text_rejects_hidden_controls_and_invalid_unicode_before_tool_budget(bridge, field, text):
    session = start(bridge)
    args = {"objective": "Improve the existing voice conversation", "acceptance": "Verify cleanup and recovery"}
    args[field] = text
    with pytest.raises(BridgeError):
        run(bridge.tool(call(session, name="prepare_mission", arguments=args), session["capability"]))
    assert bridge.session.calls == {}


@pytest.mark.parametrize("text", [
    "Préparer les voix 🚀 et 👩‍💻\n\tVérifier les interruptions\r\net la reprise",
    "تحسين المحادثة الصوتية والتحقق من الاستعادة",
])
def test_mission_text_preserves_legible_unicode_and_reviewed_line_breaks(bridge, text):
    session = start(bridge)
    result = run(bridge.tool(call(session, name="prepare_mission",
                                 arguments={"objective": text, "acceptance": text}), session["capability"]))
    assert result["draft"]["objective"] == text
    assert result["draft"]["acceptance"] == text
