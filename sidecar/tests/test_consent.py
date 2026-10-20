import asyncio
import json
import time
import types

import pytest

from starlight_voice import consent, spend, triage_bridge, x_voice
from starlight_voice.config import scrubbed_env


def test_a_refusal_anywhere_beats_a_yes() -> None:
    assert consent.verdict("yes, do it") == "yes"
    assert consent.verdict("ja genau") == "yes"
    assert consent.verdict("doe maar") == "yes"
    assert consent.verdict("no wait, yes") == "no"
    assert consent.verdict("nein") == "no"
    assert consent.verdict("hmm, which one was that") is None
    assert consent.verdict("Confirm?") is None, "the read-back's own last word is not a yes"


def test_only_speech_that_began_after_the_read_back_answers_it() -> None:
    pending = consent.Pending(kind="triage", readback="Close demo 4. Confirm?", at=100.0)
    assert consent.answer(pending, "close number four, go", spoke_at=99.0, now=101.0) is None
    assert consent.answer(pending, "yes", spoke_at=None, now=101.0) is None
    assert consent.answer(pending, "yes", spoke_at=100.5, now=101.0) == "yes"
    assert consent.answer(pending, "yes", spoke_at=100.5, now=100.0 + consent.WINDOW_S + 1) == "expired"


def test_the_microphone_hearing_ara_is_not_frank() -> None:
    readback = "Close realityarchitect 105, the jules refactor. It can be reopened. Confirm?"
    pending = consent.Pending(kind="triage", readback=readback, at=0.0)
    assert consent.answer(pending, "close realityarchitect 105 the jules refactor it can be reopened okay", spoke_at=1.0, now=2.0) is None
    assert consent.answer(pending, "okay", spoke_at=1.0, now=2.0) == "yes"


def test_the_model_never_receives_the_triage_token() -> None:
    box: dict = {}
    asked = {"ok": True, "started": False, "readback": "Close demo 4. Confirm?", "token": "abc123", "ref": "4", "action": "close"}
    out = x_voice.open_triage_consent(box, asked)
    assert "token" not in json.dumps(out)
    assert box["consent"].token == "abc123"
    failed = x_voice.open_triage_consent(box, {"ok": False, "spoken": "nope", "token": "leak"})
    assert "token" not in failed


def test_no_tool_can_carry_a_confirmation() -> None:
    tools = {t.get("name"): t for t in x_voice.session_update()["session"]["tools"] if t.get("name")}
    assert set(tools["triage_decide"]["parameters"]["properties"]) == {"ref", "action"}
    assert "triage_next" in tools


def test_a_handoff_is_confirmed_only_after_frank_was_heard(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(x_voice.approval, "confirm_spoken", lambda p: calls.append(p) or {"ok": True, "spoken": "go recorded", "proposal": p})
    box: dict = {}
    refused = x_voice.confirm_heard(box, "deadbeef00")
    assert refused["ok"] is False and calls == []
    box["heard_handoff"] = "deadbeef00"
    assert x_voice.confirm_heard(box, "deadbeef00")["ok"] is True
    assert x_voice.confirm_heard(box, "other00000")["ok"] is False
    assert x_voice.confirm_heard(None, "deadbeef00")["ok"] is False


class _Socket:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send(self, text: str) -> None:
        self.sent.append(json.loads(text))


def _run(coro):
    return asyncio.run(coro)


def test_franks_yes_records_the_triage_decision_with_the_held_token(monkeypatch) -> None:
    recorded = []
    monkeypatch.setattr(x_voice.triage_bridge, "record", lambda ref, action, token: recorded.append((ref, action, token)) or {"ok": True, "spoken": "Done. Close demo 4."})
    monkeypatch.setattr(x_voice, "append_event", lambda *a, **k: None)
    monkeypatch.setattr(x_voice, "_touch_live", lambda *a, **k: None)
    box: dict = {"consent": consent.Pending(kind="triage", ref="4", action="close", token="abc123", readback="Close demo 4. Confirm?", at=time.monotonic())}
    ws = _Socket()
    _run(x_voice._hear(ws, box, "yes", spoke_at=time.monotonic() + 0.5))
    assert recorded == [("4", "close", "abc123")]
    assert box["consent"] is None
    assert ws.sent[0]["item"]["role"] == "system"
    assert "not from Frank" in ws.sent[0]["item"]["content"][0]["text"]
    assert ws.sent[1] == {"type": "response.create"}


def test_franks_no_records_nothing(monkeypatch) -> None:
    monkeypatch.setattr(x_voice.triage_bridge, "record", lambda *a: pytest.fail("no record on a no"))
    monkeypatch.setattr(x_voice, "append_event", lambda *a, **k: None)
    monkeypatch.setattr(x_voice, "_touch_live", lambda *a, **k: None)
    box: dict = {"consent": consent.Pending(kind="triage", ref="4", action="close", token="abc123", readback="r", at=time.monotonic())}
    ws = _Socket()
    _run(x_voice._hear(ws, box, "no, keep it", spoke_at=time.monotonic() + 0.5))
    assert "Nothing was recorded" in ws.sent[0]["item"]["content"][0]["text"]


def test_a_command_said_before_the_read_back_does_not_answer_it(monkeypatch) -> None:
    monkeypatch.setattr(x_voice.triage_bridge, "record", lambda *a: pytest.fail("speech began before the read-back"))
    box: dict = {"consent": consent.Pending(kind="triage", ref="4", action="close", token="t", readback="r", at=time.monotonic())}
    ws = _Socket()
    _run(x_voice._hear(ws, box, "close four, go", spoke_at=time.monotonic() - 1.0))
    assert ws.sent == []
    assert box["consent"] is not None


def test_speech_start_is_looked_up_by_item() -> None:
    box = {"speech_starts": {"item_1": 12.5}}
    assert x_voice.speech_start(box, {"item_id": "item_1"}) == 12.5
    assert x_voice.speech_start(box, {"item_id": "item_2"}) is None
    assert x_voice.speech_start(box, {}) is None


def test_bridge_asks_then_records_with_the_token(monkeypatch, tmp_path) -> None:
    script = tmp_path / "bin" / "triage.mjs"
    script.parent.mkdir()
    script.write_text("")
    monkeypatch.setenv("STARLIGHT_TRIAGE_BIN", str(script))
    seen = []

    def runner(cmd):
        seen.append(cmd)
        if "--token" in cmd:
            return types.SimpleNamespace(returncode=0, stdout=json.dumps({"recorded": True, "seq": 7, "spoken": "Done."}))
        return types.SimpleNamespace(returncode=0, stdout=json.dumps({"pending": True, "readback": "Close demo 4. Confirm?", "token": "abc123"}))

    asked = triage_bridge.ask("4", "close", runner)
    assert asked["token"] == "abc123"
    assert triage_bridge.ask("four", "close", runner)["ok"] is False
    assert triage_bridge.ask("4", "merge", runner)["ok"] is False
    done = triage_bridge.record("4", "close", "abc123", runner)
    assert done["ok"] is True
    assert seen[-1][-6:] == ["--token", "abc123", "--source", "voice", "--json"][-5:] or "--token" in seen[-1]
    assert triage_bridge.record("4", "close", "ab;rm", runner)["ok"] is False


def test_children_get_no_credentials(monkeypatch) -> None:
    for name in ("XAI_API_KEY", "XAI_MANAGEMENT_API_KEY", "STARLIGHT_TOOL_PLANE_TOKEN", "OPENROUTER_API_KEY"):
        monkeypatch.setenv(name, "secret-not-real")
    env = scrubbed_env()
    assert not any(name in env for name in ("XAI_API_KEY", "XAI_MANAGEMENT_API_KEY", "STARLIGHT_TOOL_PLANE_TOKEN", "OPENROUTER_API_KEY"))
    assert "PATH" in env or "Path" in env
    bridge = triage_bridge.child_env()
    assert "XAI_API_KEY" not in bridge and "OPENROUTER_API_KEY" not in bridge


def test_a_failed_vault_lookup_is_asked_again(monkeypatch) -> None:
    monkeypatch.setenv("STARLIGHT_XVOICE_INFISICAL", "1")
    monkeypatch.setattr(spend, "_vault", {})
    vault = {"up": False}
    monkeypatch.setattr(spend, "infisical_secret", lambda name: {"XAI_MANAGEMENT_KEY": "mgmt-not-real", "XAI_TEAM_ID": "team-not-real"}.get(name, "") if vault["up"] else "")
    first = spend._management_creds()
    assert first == ("", "")
    vault["up"] = True
    assert spend._management_creds() == ("mgmt-not-real", "team-not-real")
