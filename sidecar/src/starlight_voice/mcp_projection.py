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

from .http_transport import verified_context

MAX_RESPONSE = 64 * 1024
PROTOCOL_VERSIONS = {"2025-03-26", "2025-06-18", "2025-11-25"}


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
        return await self._exchange(name, arguments, projection=projection)

    async def ping(self, *, expected_server: str) -> dict:
        if not isinstance(expected_server, str) or not expected_server:
            raise ProjectionUnavailable("MCP ping requires a server identity")
        return await self._exchange(None, None, expected_server=expected_server)

    async def _exchange(self, name: str | None, arguments: dict | None, *, projection: bool = False,
                        expected_server: str | None = None) -> dict:
        import httpx

        session_id = None
        in_flight = None
        cancel_request = None
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json, text/event-stream", "Content-Type": "application/json",
            "x-starlight-agent": "starlight-voice",
        }
        async with httpx.AsyncClient(
            transport=self.transport, trust_env=False, follow_redirects=False, timeout=self.deadline_s,
            verify=verified_context() if self.transport is None else True,
        ) as client:
            async def request(method: str, params: dict | None, request_id: int | None):
                nonlocal session_id, in_flight
                body = {"jsonrpc": "2.0", "method": method}
                if params is not None:
                    body["params"] = params
                if request_id is not None:
                    body["id"] = request_id
                    in_flight = request_id if method != "initialize" else None
                async with client.stream("POST", self.endpoint, headers=headers, json=body) as response:
                    response.raise_for_status()
                    returned_session = response.headers.get("mcp-session-id")
                    if returned_session is not None:
                        if not returned_session or len(returned_session) > 128 or any(not 33 <= ord(c) <= 126 for c in returned_session):
                            raise ProjectionUnavailable("Invalid MCP session")
                        if method == "initialize":
                            session_id = returned_session
                            headers["mcp-session-id"] = session_id
                        elif returned_session != session_id:
                            raise ProjectionUnavailable("MCP session changed")
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
                                        result = checked_result(message, request_id)
                                        in_flight = None
                                        return result
                    if is_sse:
                        raise ProjectionUnavailable("MCP response is incomplete")
                    message = json.loads(chunks)
                    result = checked_result(message, request_id)
                    in_flight = None
                    return result

            try:
                async with asyncio.timeout(self.deadline_s):
                    init = await request("initialize", {
                        "protocolVersion": "2025-11-25", "capabilities": {},
                        "clientInfo": {"name": "starlight-voice", "version": "0.1.0"},
                    }, 1)
                    protocol = init.get("protocolVersion")
                    if protocol not in PROTOCOL_VERSIONS:
                        raise ProjectionUnavailable("MCP protocol is unsupported")
                    headers["MCP-Protocol-Version"] = protocol
                    if expected_server and init.get("serverInfo", {}).get("name") != expected_server:
                        raise ProjectionUnavailable("MCP server identity differs")
                    if projection and init.get("serverInfo", {}).get("name") != "starlight-memory-cloud":
                        raise ProjectionUnavailable("Recall requires the signed projection gateway")
                    await request("notifications/initialized", None, None)
                    if name is None:
                        result = await request("ping", None, 3)
                        if result != {}:
                            raise ProjectionUnavailable("Invalid MCP ping result")
                        return result
                    result = await request("tools/call", {"name": name, "arguments": arguments}, 3)
                    if projection:
                        return {**result, "projection_watermark": projection_result_binding(result)}
                    return result
            except asyncio.CancelledError:
                cancel_request = in_flight
                raise
            except (TimeoutError, httpx.TimeoutException):
                cancel_request = in_flight
                raise ProjectionUnavailable("MCP read unavailable; no private fallback was attempted") from None
            except Exception:
                raise ProjectionUnavailable("MCP read unavailable; no private fallback was attempted") from None
            finally:
                if session_id or cancel_request is not None:
                    try:
                        async with asyncio.timeout(0.15):
                            if cancel_request is not None:
                                try:
                                    # Keep part of the total cleanup budget for the
                                    # owned session even if cancellation stalls.
                                    async with asyncio.timeout(0.075):
                                        await request("notifications/cancelled", {
                                            "requestId": cancel_request, "reason": "Voice read ended",
                                        }, None)
                                except Exception:
                                    pass
                            if session_id:
                                await client.delete(self.endpoint, headers=headers)
                    except Exception:
                        pass


def projection_result_binding(result: dict) -> dict:
    metadata = result.get("_meta")
    binding = metadata.get("starlight.memory.projection") if isinstance(metadata, dict) else None
    if not isinstance(binding, dict) or binding.get("schema") != "starlight.memory.projection-result.v1":
        raise ProjectionUnavailable("Memory result has no supported snapshot binding")
    for field in ("projection_id", "atoms_sha256"):
        value = binding.get(field)
        if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            raise ProjectionUnavailable("Memory result snapshot digest is invalid")
    if binding.get("policy") not in {
        "public-summaries-only", "public-and-explicitly-enabled-private-shareable-summaries-only",
    }:
        raise ProjectionUnavailable("Memory result projection policy is not approved")
    # Validate the gateway's per-result report, without claiming this client
    # has verified its manifest signature or the deployed snapshot loader.
    return {"projection_id": binding["projection_id"], "atoms_sha256": binding["atoms_sha256"],
            "policy": binding["policy"], "binding_schema": binding["schema"], "observed_before_recall": False,
            "result_binding_validated": True, "recall_snapshot_verified": False}


def checked_result(message: dict, request_id: int) -> dict:
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or type(message.get("id")) is not int or message["id"] != request_id:
        raise ProjectionUnavailable("Invalid MCP response")
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
