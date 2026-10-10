import asyncio
import json
import time

import httpx
import pytest

from starlight_voice.mcp_projection import LocalMcpReader, ProjectionUnavailable, local_mcp_endpoint


@pytest.mark.parametrize("endpoint", [
    "https://127.0.0.1:8787/mcp", "http://localhost:8787/mcp", "http://127.0.0.1/mcp",
    "http://127.0.0.1:8787/mcp?url=other", "http://127.0.0.1:8787/vault", "http://user@127.0.0.1:8787/mcp",
    "http://10.0.0.1:8787/mcp", "http://169.254.169.254:8787/mcp",
])
def test_only_explicit_numeric_loopback_mcp(endpoint):
    assert not local_mcp_endpoint(endpoint)


class Chunks(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk


def transport(*, sse=False, identity="starlight-memory-cloud", policy="public-summaries-only", oversized=False):
    requests = []

    def handler(request):
        requests.append(request)
        if request.method == "DELETE":
            return httpx.Response(200)
        body = json.loads(request.content)
        if body["method"].startswith("notifications/"):
            return httpx.Response(202)
        if body["method"] == "initialize":
            result = {"serverInfo": {"name": identity}, "protocolVersion": "2025-03-26"}
        elif body["method"] == "ping":
            result = {}
        elif body["params"]["name"] == "memory_sync_status":
            result = {"content": [{"type": "text", "text": json.dumps({"policy": policy, "projection_id": "a" * 64})}]}
        else:
            result = {"content": [{"type": "text", "text": "Approved café summary" if not oversized else "X" * 65536}]}
        value = json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": result}, ensure_ascii=False).encode()
        headers = {"mcp-session-id": "session-owned-by-test", "content-type": "application/json"}
        if sse:
            headers["content-type"] = "text/event-stream"
            value = b"event: message\ndata: " + value + b"\n\n"
            # Include a split inside a multibyte UTF-8 sequence.
            return httpx.Response(200, headers=headers, stream=Chunks([value[i:i + 1] for i in range(len(value))]))
        return httpx.Response(200, headers=headers, content=value)

    return httpx.MockTransport(handler), requests


@pytest.mark.parametrize("sse", [False, True])
def test_summary_projection_protocol_and_own_session_cleanup(sse):
    mock, requests = transport(sse=sse)
    result = asyncio.run(LocalMcpReader("http://127.0.0.1:8787/mcp", "server-only-token", transport=mock).call(
        "memory_recall", {"query": "Project", "limit": 4}, projection=True,
    ))
    assert result["content"][0]["text"] == "Approved café summary"
    assert requests[-1].method == "DELETE"
    assert all(request.headers["Authorization"] == "Bearer server-only-token" for request in requests)
    assert [json.loads(request.content)["method"] for request in requests[:-1]] == [
        "initialize", "notifications/initialized", "tools/call", "tools/call",
    ]
    assert "vault_read" not in "".join(request.content.decode() for request in requests)


@pytest.mark.parametrize("options", [{"identity": "starlight-memory"}, {"policy": "canonical-private"}, {"oversized": True}])
def test_projection_fails_closed_before_any_private_fallback(options):
    mock, requests = transport(**options)
    with pytest.raises(ProjectionUnavailable) as error:
        asyncio.run(LocalMcpReader("http://127.0.0.1:8787/mcp", "secret", transport=mock).call(
            "memory_recall", {"query": "Project"}, projection=True,
        ))
    assert "no private fallback" in str(error.value)
    assert requests[-1].method == "DELETE"
    assert not any(b"vault_read" in request.content for request in requests)


def test_total_deadline_cancels_slow_read_without_leaking_errors():
    async def delayed(request):
        await asyncio.sleep(1)
        return httpx.Response(200, json={"result": {}})

    start = time.monotonic()
    with pytest.raises(ProjectionUnavailable):
        asyncio.run(LocalMcpReader("http://127.0.0.1:8787/mcp", "secret", transport=httpx.MockTransport(delayed), deadline_s=0.03).call("memory_recall", {}))
    assert time.monotonic() - start < 0.3


def test_bearer_never_follows_redirect():
    requests = []

    def redirect(request):
        requests.append(request)
        return httpx.Response(302, headers={"Location": "https://external.example/mcp"})

    with pytest.raises(ProjectionUnavailable):
        asyncio.run(LocalMcpReader("http://127.0.0.1:8787/mcp", "secret", transport=httpx.MockTransport(redirect)).call("memory_recall", {}))
    assert len(requests) == 1


@pytest.mark.parametrize("sse", [False, True])
def test_ping_checks_advertised_identity_without_scanning_tools(sse):
    mock, requests = transport(sse=sse, identity="starlight-shared-tool-plane")
    reader = LocalMcpReader("http://127.0.0.1:8787/mcp", "secret", transport=mock)
    assert asyncio.run(reader.ping(expected_server="starlight-shared-tool-plane")) == {}
    posts = [json.loads(request.content) for request in requests if request.method == "POST"]
    assert [body["method"] for body in posts] == ["initialize", "notifications/initialized", "ping"]
    assert posts[0]["params"]["protocolVersion"] == "2025-11-25"
    assert "params" not in posts[-1]
    assert all(request.headers["MCP-Protocol-Version"] == "2025-03-26" for request in requests[1:])
    assert requests[-1].method == "DELETE"


def test_stateless_server_gets_the_negotiated_protocol_header():
    requests = []

    def handler(request):
        requests.append(request)
        body = json.loads(request.content)
        if body["method"] == "notifications/initialized":
            return httpx.Response(202)
        result = {"protocolVersion": "2025-06-18", "serverInfo": {"name": "test-server"}} if body["method"] == "initialize" else {}
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})

    asyncio.run(LocalMcpReader("http://127.0.0.1:8787/mcp", "secret", transport=httpx.MockTransport(handler)).call("safe_metadata", {}))
    assert len(requests) == 3
    assert all(request.headers["MCP-Protocol-Version"] == "2025-06-18" for request in requests[1:])


@pytest.mark.parametrize("protocol", ["2030-01-01", None])
def test_unknown_protocol_disconnects_before_any_tool_call(protocol):
    requests = []

    def handler(request):
        requests.append(request)
        if request.method == "DELETE":
            return httpx.Response(200)
        body = json.loads(request.content)
        if body["method"] == "notifications/initialized":
            return httpx.Response(202)
        result = {"protocolVersion": protocol, "serverInfo": {"name": "test"}} if body["method"] == "initialize" else {}
        return httpx.Response(200, headers={"mcp-session-id": "own-session"}, json={"jsonrpc": "2.0", "id": body["id"], "result": result})

    with pytest.raises(ProjectionUnavailable):
        asyncio.run(LocalMcpReader("http://127.0.0.1:8787/mcp", "secret", transport=httpx.MockTransport(handler)).call("safe_metadata", {}))
    assert [request.method for request in requests] == ["POST", "DELETE"]


def test_tool_response_cannot_replace_the_session_that_cleanup_owns():
    mock, requests = transport()

    async def changed_session(request):
        response = await mock.handle_async_request(request)
        if request.method == "POST" and json.loads(request.content)["method"] == "tools/call":
            response.headers["mcp-session-id"] = "another-actors-session"
        return response

    with pytest.raises(ProjectionUnavailable):
        asyncio.run(LocalMcpReader("http://127.0.0.1:8787/mcp", "secret", transport=httpx.MockTransport(changed_session)).call("safe_metadata", {}))
    assert requests[-1].method == "DELETE"
    assert requests[-1].headers["mcp-session-id"] == "session-owned-by-test"
    assert all(request.headers.get("mcp-session-id") != "another-actors-session" for request in requests)


def test_ping_requires_its_expected_identity():
    mock, requests = transport(identity="starlight-memory-cloud")
    with pytest.raises(ProjectionUnavailable):
        asyncio.run(LocalMcpReader("http://127.0.0.1:8787/mcp", "secret", transport=mock).ping(expected_server="starlight-shared-tool-plane"))
    assert [request.method for request in requests] == ["POST", "DELETE"]


def test_ping_timeout_cancels_only_its_own_request_then_closes_its_session():
    requests = []

    async def handler(request):
        requests.append(request)
        if request.method == "DELETE":
            return httpx.Response(200)
        body = json.loads(request.content)
        if body["method"].startswith("notifications/"):
            return httpx.Response(202)
        if body["method"] == "ping":
            await asyncio.sleep(1)
        result = {"protocolVersion": "2025-03-26", "serverInfo": {"name": "starlight-shared-tool-plane"}}
        return httpx.Response(200, headers={"mcp-session-id": "own-session"}, json={"jsonrpc": "2.0", "id": body["id"], "result": result})

    started = time.monotonic()
    with pytest.raises(ProjectionUnavailable):
        asyncio.run(LocalMcpReader("http://127.0.0.1:8787/mcp", "secret", transport=httpx.MockTransport(handler), deadline_s=.03).ping(expected_server="starlight-shared-tool-plane"))
    assert time.monotonic() - started < .3
    cancel = [json.loads(request.content) for request in requests if request.method == "POST" and json.loads(request.content)["method"] == "notifications/cancelled"]
    assert len(cancel) == 1 and cancel[0]["params"]["requestId"] == 3
    assert requests[-1].method == "DELETE" and requests[-1].headers["mcp-session-id"] == "own-session"


def test_caller_cancellation_during_cleanup_is_not_swallowed():
    async def scenario():
        entered = asyncio.Event()
        mock, _ = transport()

        async def handler(request):
            if request.method == "DELETE":
                entered.set()
                await asyncio.sleep(1)
            return await mock.handle_async_request(request)

        reader = LocalMcpReader("http://127.0.0.1:8787/mcp", "secret", transport=httpx.MockTransport(handler))
        task = asyncio.create_task(reader.call("safe_metadata", {}))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())


@pytest.mark.parametrize("sse", [False, True])
def test_ping_rejects_nonempty_results(sse):
    mock, requests = transport(sse=sse, identity="starlight-shared-tool-plane")

    async def handler(request):
        if request.method == "POST" and json.loads(request.content)["method"] == "ping":
            requests.append(request)
            value = json.dumps({"jsonrpc": "2.0", "id": 3, "result": {"unverified": "health"}})
            if sse:
                return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=f"data: {value}\n\n")
            return httpx.Response(200, content=value)
        return await mock.handle_async_request(request)

    with pytest.raises(ProjectionUnavailable):
        asyncio.run(LocalMcpReader("http://127.0.0.1:8787/mcp", "secret", transport=httpx.MockTransport(handler)).ping(expected_server="starlight-shared-tool-plane"))
    assert requests[-1].method == "DELETE"


def test_http_timeout_and_rejected_cancellation_still_close_the_owned_session():
    mock, requests = transport(identity="starlight-shared-tool-plane")

    async def handler(request):
        body = json.loads(request.content) if request.method == "POST" else {}
        if body.get("method") in {"ping", "notifications/cancelled"}:
            requests.append(request)
            if body["method"] == "ping":
                raise httpx.ReadTimeout("private endpoint detail", request=request)
            return httpx.Response(500)
        return await mock.handle_async_request(request)

    with pytest.raises(ProjectionUnavailable) as error:
        asyncio.run(LocalMcpReader("http://127.0.0.1:8787/mcp", "secret", transport=httpx.MockTransport(handler)).ping(expected_server="starlight-shared-tool-plane"))
    assert "private endpoint" not in str(error.value)
    assert json.loads(requests[-2].content)["params"]["requestId"] == 3
    assert requests[-1].method == "DELETE" and requests[-1].headers["mcp-session-id"] == "session-owned-by-test"


def test_initialize_timeout_never_sends_a_cancellation_notification():
    requests = []

    async def handler(request):
        requests.append(request)
        await asyncio.sleep(1)

    with pytest.raises(ProjectionUnavailable):
        asyncio.run(LocalMcpReader("http://127.0.0.1:8787/mcp", "secret", transport=httpx.MockTransport(handler), deadline_s=.03).call("safe_metadata", {}))
    assert len(requests) == 1 and json.loads(requests[0].content)["method"] == "initialize"


def test_caller_cancellation_during_ping_cancels_the_request_and_closes_its_session():
    async def scenario():
        entered = asyncio.Event()
        mock, requests = transport(identity="starlight-shared-tool-plane")

        async def handler(request):
            if request.method == "POST" and json.loads(request.content)["method"] == "ping":
                entered.set()
                await asyncio.sleep(1)
            return await mock.handle_async_request(request)

        reader = LocalMcpReader("http://127.0.0.1:8787/mcp", "secret", transport=httpx.MockTransport(handler))
        task = asyncio.create_task(reader.ping(expected_server="starlight-shared-tool-plane"))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert json.loads(requests[-2].content)["method"] == "notifications/cancelled"
        assert requests[-1].method == "DELETE"

    asyncio.run(scenario())
