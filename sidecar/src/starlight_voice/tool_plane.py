"""Read-only client for the shared Starlight tool plane (MCP over HTTP on 127.0.0.1:7317).

Ara joins as agent ``starlight-voice``. Only the tools in ``READ_ONLY`` can be called, so a
misheard sentence can read memory and estate state but never change anything.
"""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

PLANE_URL = "http://127.0.0.1:7317/mcp"
AGENT = "starlight-voice"
READ_ONLY = frozenset({
    "memory__memory_recall",
    "memory__memory_search",
    "starlight_bridge__list_tasks",
    "starlight_bridge__get_system_telemetry",
})
# One call measured 1.2 s on 2026-10-19. Past this, Ara says memory is slow instead of stalling.
TIMEOUT_S = 4.0


class PlaneError(RuntimeError):
    """Carries a short reason only. Never the bearer token or a response body."""


def _url() -> str:
    return os.environ.get("STARLIGHT_TOOL_PLANE_URL", "").strip() or PLANE_URL


def _token() -> str:
    override = os.environ.get("STARLIGHT_TOOL_PLANE_TOKEN", "").strip()
    if override:
        return override
    path = Path.home() / ".starlight" / "tool-plane" / "bearer.token"
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def enabled() -> bool:
    return os.environ.get("STARLIGHT_XVOICE_PLANE", "1").strip() != "0"


def _payload(response) -> dict:
    kind = response.headers.get("content-type", "")
    if kind.startswith("text/event-stream"):
        for line in response.text.splitlines():
            if line.startswith("data:"):
                return json.loads(line[5:])
        return {}
    return response.json() if response.text else {}


def call(name: str, arguments: dict, *, post=None) -> str:
    """Run one read-only plane tool and return its text. ``post(url, headers, body)`` is the test seam."""

    if name not in READ_ONLY:
        raise PlaneError("not-read-only")
    if not enabled():
        raise PlaneError("off")
    token = _token()
    if not token:
        raise PlaneError("no-token")
    headers = {
        "Authorization": f"Bearer {token}",
        "x-starlight-agent": AGENT,
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    send = post or _httpx_post
    try:
        opened = send(_url(), headers, {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                       "clientInfo": {"name": AGENT, "version": "1"}},
        })
        session = opened.headers.get("mcp-session-id")
        if session:
            headers["Mcp-Session-Id"] = session
        send(_url(), headers, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        reply = _payload(send(_url(), headers, {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }))
    except PlaneError:
        raise
    except Exception as exc:
        raise PlaneError(type(exc).__name__) from None
    if not isinstance(reply, dict) or "error" in reply:
        raise PlaneError("tool-error")
    result = reply.get("result") if isinstance(reply.get("result"), dict) else {}
    if result.get("isError"):
        raise PlaneError("tool-error")
    parts = result.get("content") if isinstance(result.get("content"), list) else []
    return "".join(str(part.get("text") or "") for part in parts if isinstance(part, dict)).strip()


def _httpx_post(url: str, headers: dict, body: dict):
    import httpx

    return httpx.post(url, headers=headers, json=body, timeout=TIMEOUT_S)


def clip(text: str, limit: int) -> str:
    return " ".join(text.split())[:limit]


STATUS_TOOLS = {
    "queue": ("starlight_bridge__list_tasks", {}),
    "machine": ("starlight_bridge__get_system_telemetry", {}),
}
# get_active_sessions took over 4 s on 2026-10-19; a voice turn cannot wait that long.
_SLOW = "Memory did not answer in time. I'll go on without it."


def recall(query: str, *, runner=call) -> dict[str, object]:
    """Vault recall for a voice turn. The text is data from the vault, not instructions."""

    text = " ".join(str(query).split())
    if len(text) < 3:
        return {"ok": False, "started": False, "spoken": "Say a little more about what to recall."}
    try:
        found = runner("memory__memory_recall", {"query": text[:200], "limit": 4})
    except PlaneError:
        return {"ok": False, "started": False, "spoken": _SLOW}
    if not found:
        return {"ok": False, "started": False, "spoken": "Nothing in memory matches that."}
    return {"ok": True, "started": False, "untrusted": True, "memory": clip(found, 1200), "spoken": ""}


def estate_status(kind: str, *, runner=call) -> dict[str, object]:
    """Read the queue, the machine, or the live agent sessions. Never changes them."""

    picked = STATUS_TOOLS.get(str(kind).strip().lower())
    if picked is None:
        return {"ok": False, "started": False, "spoken": "I can read the queue or the machine."}
    try:
        found = runner(*picked)
    except PlaneError:
        return {"ok": False, "started": False, "spoken": f"The {kind} status did not answer."}
    return {"ok": bool(found), "started": False, "untrusted": True, "status": clip(found, 1200), "spoken": ""}


def opening_brief(*, runner=call) -> str:
    """What Ara knows before Frank speaks: this week's plan and the voice work itself."""

    def one(query: str) -> str:
        try:
            return clip(runner("memory__memory_recall", {"query": query, "limit": 2}), 450)
        except PlaneError:
            return ""

    queries = ("this week's plan and priorities", "voice operator Ara current state")
    with ThreadPoolExecutor(max_workers=len(queries)) as pool:
        blocks = list(pool.map(one, queries))
    return " | ".join(block for block in blocks if block)
