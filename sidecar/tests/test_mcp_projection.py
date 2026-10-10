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
        if body["method"] == "notifications/initialized":
            return httpx.Response(202)
        if body["method"] == "initialize":
            result = {"serverInfo": {"name": identity}, "protocolVersion": "2025-03-26"}
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
