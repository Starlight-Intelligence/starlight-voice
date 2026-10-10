"""Bounded Streamable HTTP MCP reads; canonical vault tools are never voice tools.

The memory endpoint must be the existing signed cloud-projection gateway. That
gateway verifies its Ed25519 manifest and atom digest; this client does not
duplicate its vault loader or silently fall back to the private shared backend.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
from urllib.parse import urlsplit

MAX_RESPONSE = 64 * 1024


def local_mcp_endpoint(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        return (
            parsed.scheme == "http" and ipaddress.ip_address(parsed.hostname).is_loopback
            and parsed.port is not None and parsed.path == "/mcp"
            and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment
        )
    except (ValueError, TypeError):
        return False


class ProjectionUnavailable(Exception):
    """An intentionally redacted failure, safe to return to a speech provider."""


class LocalMcpReader:
    def __init__(self, endpoint: str, token: str, *, transport=None, deadline_s: float = 2.0):
        if not local_mcp_endpoint(endpoint) or not token:
            raise ProjectionUnavailable("MCP endpoint is not configured safely")
        self.endpoint, self.token = endpoint, token
        self.transport, self.deadline_s = transport, deadline_s

    async def call(self, name: str, arguments: dict, *, projection: bool = False) -> dict:
        import httpx

        session_id = None
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json, text/event-stream", "Content-Type": "application/json",
            "x-starlight-agent": "starlight-voice",
        }
        async with httpx.AsyncClient(
            transport=self.transport, trust_env=False, follow_redirects=False, timeout=self.deadline_s,
        ) as client:
            async def request(method: str, params: dict | None, request_id: int | None):
                nonlocal session_id
                body = {"jsonrpc": "2.0", "method": method}
                if params is not None:
                    body["params"] = params
                if request_id is not None:
                    body["id"] = request_id
                async with client.stream("POST", self.endpoint, headers=headers, json=body) as response:
                    response.raise_for_status()
                    if response.headers.get("mcp-session-id"):
                        session_id = response.headers["mcp-session-id"]
                        if len(session_id) > 128 or any(ord(c) < 32 for c in session_id):
                            raise ProjectionUnavailable("Invalid MCP session")
                        headers["mcp-session-id"] = session_id
                        headers["MCP-Protocol-Version"] = "2025-03-26"
                    if request_id is None:
                        return {}
                    chunks = bytearray()
                    is_sse = response.headers.get("content-type", "").startswith("text/event-stream")
                    async for chunk in response.aiter_bytes():
                        chunks.extend(chunk)
                        if len(chunks) > MAX_RESPONSE:
                            raise ProjectionUnavailable("MCP result exceeds the voice limit")
                        if is_sse:
                            # End the stream as soon as our response arrives, even
                            # if a server holds its SSE connection open afterward.
                            try:
                                normalized = chunks.decode("utf-8").replace("\r\n", "\n")
                            except UnicodeDecodeError as error:
                                if error.reason == "unexpected end of data":
                                    continue
                                raise
                            for frame in normalized.split("\n\n")[:-1]:
                                data = "\n".join(line[5:].lstrip() for line in frame.splitlines() if line.startswith("data:"))
                                if data:
                                    message = json.loads(data)
                                    if message.get("id") == request_id:
                                        return checked_result(message)
                    if is_sse:
                        raise ProjectionUnavailable("MCP response is incomplete")
                    message = json.loads(chunks)
                    if message.get("id") != request_id:
                        raise ProjectionUnavailable("MCP response ID mismatch")
                    return checked_result(message)

            try:
                async with asyncio.timeout(self.deadline_s):
                    init = await request("initialize", {
                        "protocolVersion": "2025-03-26", "capabilities": {},
                        "clientInfo": {"name": "starlight-voice", "version": "0.1.0"},
                    }, 1)
                    if projection and init.get("serverInfo", {}).get("name") != "starlight-memory-cloud":
                        raise ProjectionUnavailable("Recall requires the signed projection gateway")
                    await request("notifications/initialized", None, None)
                    if projection:
                        watermark = await request("tools/call", {"name": "memory_sync_status", "arguments": {}}, 2)
                        status = json.loads(tool_text(watermark))
                        if status.get("policy") not in {
                            "public-summaries-only", "public-and-explicitly-enabled-private-shareable-summaries-only",
                        }:
                            raise ProjectionUnavailable("Memory projection policy is not approved")
                        if not isinstance(status.get("projection_id"), str) or len(status["projection_id"]) != 64:
                            raise ProjectionUnavailable("Memory projection watermark is missing")
                    return await request("tools/call", {"name": name, "arguments": arguments}, 3)
            except asyncio.CancelledError:
                raise
            except Exception:
                raise ProjectionUnavailable("MCP read unavailable; no private fallback was attempted") from None
            finally:
                if session_id:
                    try:
                        async with asyncio.timeout(0.15):
                            await client.delete(self.endpoint, headers=headers)
                    except (Exception, asyncio.CancelledError):
                        pass


def checked_result(message: dict) -> dict:
    if "error" in message or not isinstance(message.get("result"), dict):
        raise ProjectionUnavailable("MCP read failed")
    result = message["result"]
    if result.get("isError"):
        raise ProjectionUnavailable("MCP tool failed")
    return result


def tool_text(result: dict) -> str:
    content = result.get("content", [])
    if not isinstance(content, list):
        raise ProjectionUnavailable("Invalid MCP content")
    text = "\n".join(item["text"] for item in content if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str))
    if len(text) > 8192:
        raise ProjectionUnavailable("MCP text exceeds the voice limit")
    return text
