"""Transport tests: a real socket, a real SSE stream, no mocks of the wire.

If these pass, a CopilotKit client, curl, or the tray can consume the stream.
"""

from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from starlight_voice import agui_events as agui
from starlight_voice.agui_server import Runtime, serve, status_reply


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.httpd = serve(port=0)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.httpd.server_address[:2]
        self.base = f"http://{host}:{port}"

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)

    def post(self, path, payload):
        body = json.dumps(payload).encode("utf-8") if not isinstance(payload, bytes) else payload
        req = urllib.request.Request(
            self.base + path, data=body, headers={"Content-Type": "application/json"}, method="POST"
        )
        return urllib.request.urlopen(req, timeout=10)

    def test_health_reports_the_transport_and_schema(self):
        with urllib.request.urlopen(self.base + "/health", timeout=5) as resp:
            payload = json.loads(resp.read())
        self.assertEqual(resp.status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["transport"], "sse")
        self.assertEqual(payload["schema"], agui.SCHEMA_VERSION)

    def test_run_streams_a_valid_event_sequence(self):
        with self.post("/agui", {"threadId": "t1", "runId": "r1", "message": "status please"}) as resp:
            self.assertEqual(resp.status, 200)
            self.assertTrue(resp.headers["Content-Type"].startswith("text/event-stream"))
            raw = resp.read().decode("utf-8")

        events = [json.loads(line[len("data: "):]) for line in raw.splitlines() if line.startswith("data: ")]
        self.assertGreaterEqual(len(events), 5)
        self.assertEqual(events[0]["type"], agui.EventType.RUN_STARTED)
        self.assertEqual(events[-1]["type"], agui.EventType.RUN_FINISHED)
        self.assertEqual(agui.validate_stream(events), [])

    def test_each_event_is_blank_line_separated(self):
        with self.post("/agui", {"threadId": "t1", "runId": "r2", "message": "hi"}) as resp:
            raw = resp.read().decode("utf-8")
        self.assertTrue(raw.endswith("\n\n"))
        self.assertEqual(raw.count("\n\n"), raw.count("data: "))

    def test_missing_run_id_is_a_400(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.post("/agui", {"threadId": "t1", "message": "hi"})
        self.assertEqual(ctx.exception.code, 400)
        body = json.loads(ctx.exception.read())
        self.assertIn("runId", body["error"])

    def test_bad_json_is_a_400(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.post("/agui", b"{not json")
        self.assertEqual(ctx.exception.code, 400)

    def test_unknown_path_is_a_404(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.post("/nope", {"threadId": "t", "runId": "r", "message": "x"})
        self.assertEqual(ctx.exception.code, 404)

    def test_empty_message_still_produces_a_balanced_stream(self):
        with self.post("/agui", {"threadId": "t1", "runId": "r3", "message": ""}) as resp:
            raw = resp.read().decode("utf-8")
        events = [json.loads(line[6:]) for line in raw.splitlines() if line.startswith("data: ")]
        self.assertEqual(agui.validate_stream(events), [])

    def test_runtime_rejects_an_invalid_reply_instead_of_streaming_it(self):
        runtime = Runtime(reply_fn=lambda _m: {"reply": 42})
        with self.assertRaises(ValueError):
            runtime.handle({"threadId": "t", "runId": "r", "message": "x"})

    def test_status_reply_makes_no_claims(self):
        reply = status_reply("build me a house")
        self.assertEqual(reply["mode"], "bounded-status-router")
        self.assertIsNone(reply["proposedTask"])
        self.assertIn("nothing was interpreted", reply["reply"])


class CapturedStreamTests(unittest.TestCase):
    """The committed evidence must still be a valid stream.

    docs/evidence/agui-stream-20261113.txt was captured from a real server on
    c940. If the protocol changes shape, that file stops being evidence - this
    test notices instead of leaving a stale artefact in the repo.
    """

    def test_committed_capture_is_a_valid_stream(self):
        evidence = Path(__file__).resolve().parents[2] / "docs" / "evidence" / "agui-stream-20261113.txt"
        self.assertTrue(evidence.exists(), f"missing capture: {evidence}")
        raw = evidence.read_text(encoding="utf-8")
        events = [json.loads(line[6:]) for line in raw.splitlines() if line.startswith("data: ")]
        self.assertGreaterEqual(len(events), 5)
        self.assertEqual(agui.validate_stream(events), [])
        self.assertEqual(events[0]["type"], agui.EventType.RUN_STARTED)
        self.assertEqual(events[-1]["type"], agui.EventType.RUN_FINISHED)

    def test_capture_reconstructs_the_message(self):
        evidence = Path(__file__).resolve().parents[2] / "docs" / "evidence" / "agui-stream-20261113.txt"
        raw = evidence.read_text(encoding="utf-8")
        events = [json.loads(line[6:]) for line in raw.splitlines() if line.startswith("data: ")]
        text = "".join(
            e["delta"] for e in events if e["type"] == agui.EventType.TEXT_MESSAGE_CONTENT
        )
        self.assertIn("transport is live", text)
        self.assertIn("nothing was interpreted", text)


if __name__ == "__main__":
    unittest.main()
