from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, TextIO

from .browser import BrowserAutomationAdapter
from .pipeline import AgentPipeline


@dataclass(frozen=True)
class IpcError:
    code: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


class JsonLineIpcServer:
    """Small JSON-lines RPC server for the Rust tray process.

    Requests:
      {"id":"1","method":"health","params":{}}
      {"id":"2","method":"utterance","params":{"text":"hello"}}
      {"id":"3","method":"browser.task","params":{"goal":"open docs","live":false}}
    """

    def __init__(self, *, pipeline: AgentPipeline) -> None:
        self.pipeline = pipeline

    def serve(self, stdin: TextIO, stdout: TextIO) -> int:
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            response = self.handle_line(line)
            stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
            stdout.flush()
        return 0

    def handle_line(self, line: str) -> dict[str, Any]:
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            return self._error(None, "invalid-json", str(exc))

        request_id = request.get("id")
        method = request.get("method")
        params = request.get("params") or {}
        if not isinstance(params, dict):
            return self._error(request_id, "invalid-params", "params must be an object")

        if method == "health":
            return self._result(request_id, self.pipeline.health())

        if method == "utterance":
            text = params.get("text")
            if not isinstance(text, str):
                return self._error(request_id, "invalid-params", "utterance.text must be a string")
            return self._result(request_id, self.pipeline.process_text(text))

        if method == "browser.task":
            goal = params.get("goal")
            live = bool(params.get("live", False))
            if not isinstance(goal, str):
                return self._error(request_id, "invalid-params", "browser.task.goal must be a string")
            result = BrowserAutomationAdapter(live=live).run(goal)
            return self._result(request_id, result.to_dict())

        if method == "ping":
            return self._result(request_id, {"pong": True})

        if method == "session.start":
            session_id = params.get("session_id", "default")
            return self._result(request_id, {"session_id": session_id, "status": "active"})

        if method == "session.stop":
            session_id = params.get("session_id", "default")
            return self._result(request_id, {"session_id": session_id, "status": "terminated"})

        if method == "shutdown":
            return self._result(request_id, {"status": "shutting_down"})

        return self._error(request_id, "unknown-method", f"Unknown method: {method}")

    @staticmethod
    def _result(request_id: object, result: dict[str, Any]) -> dict[str, Any]:
        return {"id": request_id, "ok": True, "result": result}

    @staticmethod
    def _error(request_id: object, code: str, message: str) -> dict[str, Any]:
        return {"id": request_id, "ok": False, "error": IpcError(code, message).to_dict()}
