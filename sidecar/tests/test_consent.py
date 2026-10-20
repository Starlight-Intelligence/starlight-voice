import asyncio
import json
import time
import types

import pytest

from starlight_voice import consent, spend, triage_bridge, x_voice
from starlight_voice.config import scrubbed_env


def test_only_a_short_explicit_answer_counts() -> None:
    assert consent.verdict("yes") == "yes"
    assert consent.verdict("yes, close it") == "yes"
    assert consent.verdict("ja genau") == "yes"
    assert consent.verdict("doe maar") == "yes"
    assert consent.verdict("go ahead") == "yes"
    assert consent.verdict("I was wondering where to go for lunch") is None
    assert consent.verdict("sure thing let's maybe look again later") is None, "too long to be an answer"
    assert consent.verdict("go") is None
    assert consent.verdict("Confirm?") is None, "the read-back's own last word is not a yes"


def test_a_refusal_anywhere_beats_a_yes() -> None:
    assert consent.verdict("no wait, yes") == "no"
    assert consent.verdict("yes, not that one") == "no"
    assert consent.verdict("nein") == "no"


def _armed(at: float) -> consent.Pending:
    pending = consent.Pending(kind="triage", readback="Close demo 4. Confirm?")
    pending.arm(playback_until=at - consent.ECHO_TAIL_S, now=at - consent.ECHO_TAIL_S)
    return pending


def test_nothing_answers_before_the_read_back_has_played() -> None:
    pending = consent.Pending(kind="triage", readback="Close demo 4. Confirm?")
    now = time.monotonic()
    assert consent.answer(pending, "yes", spoke_at=now + 5, now=now + 5) is None, "not armed yet"
    pending.arm(playback_until=now + 3.0, now=now)
    assert pending.at == pytest.approx(now + 3.0 + consent.ECHO_TAIL_S)
    assert consent.answer(pending, "yes", spoke_at=now + 2.0, now=now + 6) is None, "began during playback"
    assert consent.answer(pending, "yes", spoke_at=now + 3.5, now=now + 6) is None, "began in the echo tail"
    assert consent.answer(pending, "yes", spoke_at=None, now=now + 6) is None, "unknown start never counts"
    assert consent.answer(pending, "yes", spoke_at=now + 5.0, now=now + 6) == "yes"
    assert consent.answer(pending, "yes", spoke_at=now + 5.0, now=pending.at + consent.WINDOW_S + 1) == "expired"


def test_speech_during_aras_audio_is_marked_ineligible() -> None:
    box: dict = {}
    x_voice.note_playback(box, pcm_bytes=48_000 * 2, now=100.0)  # two seconds at 24 kHz, 16-bit
    assert box["playback_until"] == pytest.approx(102.0)
    assert x_voice.eligible_start(box, 101.0) is None
    assert x_voice.eligible_start(box, 103.0) is None, "inside the echo tail"
    assert x_voice.eligible_start(box, 104.0) == 104.0


def test_the_answer_window_arms_after_the_read_back_response() -> None:
    box: dict = {"consent": consent.Pending(kind="triage", readback="r"), "playback_until": 50.0}
    x_voice.arm_after_readback(box, now=48.0)
    assert box["consent"].armed is True
    assert box["consent"].at == pytest.approx(50.0 + consent.ECHO_TAIL_S)
    x_voice.arm_after_readback(box, now=60.0)
    assert box["consent"].at == pytest.approx(50.0 + consent.ECHO_TAIL_S), "arming happens once"


def test_the_model_never_receives_the_triage_token() -> None:
    box: dict = {}
    asked = {"ok": True, "started": False, "readback": "Close demo 4. Confirm?", "token": "abc123", "ref": "4", "action": "close"}
    out = x_voice.open_triage_consent(box, asked)
    assert "abc123" not in json.dumps(out)
    assert box["consent"].token == "abc123"
    assert box["consent"].armed is False
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
    assert x_voice.confirm_heard(box, "deadbeef00")["ok"] is False and calls == []
    box["heard_handoff"] = "deadbeef00"
    assert x_voice.confirm_heard(box, "deadbeef00")["ok"] is True
    assert x_voice.confirm_heard(box, "other00000")["ok"] is False
    assert x_voice.confirm_heard(None, "deadbeef00")["ok"] is False


class _Socket:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send(self, text: str) -> None:
        self.sent.append(json.loads(text))


def _quiet(monkeypatch) -> None:
    monkeypatch.setattr(x_voice, "append_event", lambda *a, **k: None)
    monkeypatch.setattr(x_voice, "_touch_live", lambda *a, **k: None)


def test_franks_yes_records_the_triage_decision_with_the_held_token(monkeypatch) -> None:
    recorded = []
    monkeypatch.setattr(x_voice.triage_bridge, "record", lambda ref, action, token: recorded.append((ref, action, token)) or {"ok": True, "spoken": "Done. Close demo 4."})
    _quiet(monkeypatch)
    now = time.monotonic()
    pending = _armed(now)
    pending.ref, pending.action, pending.token = "4", "close", "abc123"
    box: dict = {"consent": pending}
    ws = _Socket()
    asyncio.run(x_voice._hear(ws, box, "yes", spoke_at=now + 0.5))
    assert recorded == [("4", "close", "abc123")]
    assert box["consent"] is None
    assert ws.sent[0]["item"]["role"] == "system"
    assert "not from Frank" in ws.sent[0]["item"]["content"][0]["text"]
    assert ws.sent[1] == {"type": "response.create"}


def test_franks_no_records_nothing(monkeypatch) -> None:
    monkeypatch.setattr(x_voice.triage_bridge, "record", lambda *a: pytest.fail("no record on a no"))
    _quiet(monkeypatch)
    now = time.monotonic()
    box: dict = {"consent": _armed(now)}
    ws = _Socket()
    asyncio.run(x_voice._hear(ws, box, "no, keep it", spoke_at=now + 0.5))
    assert "Nothing was recorded" in ws.sent[0]["item"]["content"][0]["text"]


def test_her_own_yes_through_the_speakers_records_nothing(monkeypatch) -> None:
    monkeypatch.setattr(x_voice.triage_bridge, "record", lambda *a: pytest.fail("Ara's own audio is not consent"))
    now = time.monotonic()
    box: dict = {"consent": _armed(now)}
    x_voice.note_playback(box, pcm_bytes=24_000 * 2, now=now + 1.0)  # she starts talking again
    start = x_voice.eligible_start(box, now + 1.2)
    assert start is None
    ws = _Socket()
    asyncio.run(x_voice._hear(ws, box, "yes", spoke_at=start))
    assert ws.sent == []
    assert box["consent"] is not None


def test_speech_start_is_looked_up_by_item() -> None:
    box = {"speech_starts": {"item_1": 12.5, "item_2": None}}
    assert x_voice.speech_start(box, {"item_id": "item_1"}) == 12.5
    assert x_voice.speech_start(box, {"item_id": "item_2"}) is None
    assert x_voice.speech_start(box, {"item_id": "item_3"}) is None
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

    assert triage_bridge.ask("4", "close", runner)["token"] == "abc123"
    assert triage_bridge.ask("four", "close", runner)["ok"] is False
    assert triage_bridge.ask("4", "merge", runner)["ok"] is False
    assert triage_bridge.record("4", "close", "abc123", runner)["ok"] is True
    last = seen[-1]
    assert last[last.index("--token") + 1] == "abc123"
    assert last[last.index("--source") + 1] == "voice"
    assert triage_bridge.record("4", "close", "ab;rm", runner)["ok"] is False


def test_bridge_next_passes_only_named_fields(monkeypatch, tmp_path) -> None:
    script = tmp_path / "triage.mjs"
    script.write_text("")
    monkeypatch.setenv("STARLIGHT_TRIAGE_BIN", str(script))
    def leaky(cmd):
        return types.SimpleNamespace(returncode=0, stdout=json.dumps({"remaining": 2, "spoken": "1. demo 4.", "token": "leak", "items": [{"key": "x"}]}))

    def odd(cmd):
        return types.SimpleNamespace(returncode=0, stdout=json.dumps({"token": "leak"}))

    out = triage_bridge.next_items(3, leaky)
    assert set(out) == {"ok", "started", "remaining", "spoken"}
    assert "leak" not in json.dumps(triage_bridge.next_items(3, odd))


def test_children_get_no_credentials(monkeypatch) -> None:
    for name in ("XAI_API_KEY", "XAI_MANAGEMENT_API_KEY", "STARLIGHT_TOOL_PLANE_TOKEN", "OPENROUTER_API_KEY"):
        monkeypatch.setenv(name, "secret-not-real")
    env = scrubbed_env()
    assert not any(name in env for name in ("XAI_API_KEY", "XAI_MANAGEMENT_API_KEY", "STARLIGHT_TOOL_PLANE_TOKEN", "OPENROUTER_API_KEY"))
    bridge = triage_bridge.child_env()
    assert "XAI_API_KEY" not in bridge and "OPENROUTER_API_KEY" not in bridge


def test_a_failed_vault_lookup_is_asked_again(monkeypatch) -> None:
    monkeypatch.setenv("STARLIGHT_XVOICE_INFISICAL", "1")
    monkeypatch.setattr(spend, "_vault", {})
    vault = {"up": False}
    monkeypatch.setattr(spend, "infisical_secret", lambda name: {"XAI_MANAGEMENT_KEY": "mgmt-not-real", "XAI_TEAM_ID": "team-not-real"}.get(name, "") if vault["up"] else "")
    assert spend._management_creds() == ("", "")
    vault["up"] = True
    assert spend._management_creds() == ("mgmt-not-real", "team-not-real")


def test_a_failed_voice_key_lookup_is_asked_again(monkeypatch) -> None:
    monkeypatch.setenv("STARLIGHT_XVOICE_INFISICAL", "1")
    monkeypatch.setattr(x_voice, "_pulled", None)
    monkeypatch.setattr(x_voice, "_pulled_set", False)
    answers = iter(["", "xai-test-not-real-key"])
    monkeypatch.setattr(x_voice, "_infisical_stdout", lambda: next(answers))
    assert x_voice.key_from_infisical() is None
    assert x_voice.key_from_infisical() == "xai-test-not-real-key"
