import json

from starlight_voice.ipc import JsonLineIpcServer
from starlight_voice.pipeline import AgentPipeline


def test_ipc_health() -> None:
    server = JsonLineIpcServer(pipeline=AgentPipeline())

    response = server.handle_line(json.dumps({"id": "1", "method": "health", "params": {}}))

    assert response["ok"] is True
    assert response["result"]["status"] == "ok"


def test_ipc_utterance_routes_browser_task() -> None:
    server = JsonLineIpcServer(pipeline=AgentPipeline())

    response = server.handle_line(json.dumps({"id": "2", "method": "utterance", "params": {"text": "open browser and search"}}))

    assert response["ok"] is True
    assert response["result"]["route"]["tier"] == "tier3-browser"


def test_ipc_rejects_unknown_method() -> None:
    server = JsonLineIpcServer(pipeline=AgentPipeline())

    response = server.handle_line(json.dumps({"id": "3", "method": "nope", "params": {}}))

    assert response["ok"] is False
    assert response["error"]["code"] == "unknown-method"


def test_ipc_lifecycle() -> None:
    server = JsonLineIpcServer(pipeline=AgentPipeline())

    ping_res = server.handle_line(json.dumps({"id": "4", "method": "ping", "params": {}}))
    assert ping_res["ok"] is True
    assert ping_res["result"]["pong"] is True

    start_res = server.handle_line(json.dumps({"id": "5", "method": "session.start", "params": {"session_id": "sess-42"}}))
    assert start_res["ok"] is True
    assert start_res["result"]["status"] == "active"
    assert start_res["result"]["session_id"] == "sess-42"

    stop_res = server.handle_line(json.dumps({"id": "6", "method": "session.stop", "params": {"session_id": "sess-42"}}))
    assert stop_res["ok"] is True
    assert stop_res["result"]["status"] == "terminated"

    shutdown_res = server.handle_line(json.dumps({"id": "7", "method": "shutdown", "params": {}}))
    assert shutdown_res["ok"] is True
    assert shutdown_res["result"]["status"] == "shutting_down"

