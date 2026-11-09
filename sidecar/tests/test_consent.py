import asyncio
import json
import time
import types

import pytest

from starlight_voice import consent, spend, triage_bridge, x_voice
from starlight_voice.config import scrubbed_env


def test_only_a_short_explicit_answer_counts() -> None:
    assert consent.verdict("yes") == "yes"
    assert consent.verdict("yes, close it", action="close") == "yes"
    assert consent.verdict("ja genau") == "yes"
    assert consent.verdict("doe maar") == "yes"
    assert consent.verdict("go ahead") == "yes"
    assert consent.verdict("I was wondering where to go for lunch") is None
    assert consent.verdict("sure thing let's maybe look again later") is None, "too long to be an answer"
    assert consent.verdict("go") is None
    assert consent.verdict("Confirm?") is None, "the read-back's own last word is not a yes"
    assert consent.verdict("okay where is my phone") is None
    assert consent.verdict("yes later") is None
    assert consent.verdict("yes close number 4", action="close", allowed_numbers={"4"}) == "yes"
    assert consent.verdict("ja bitte") == "yes"


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


CANON = "Close demo pull request 451. Say yes to confirm, or no."


def _close_451() -> consent.Pending:
    return consent.Pending(
        kind="triage", readback='Close demo 451, "Fix 2 bugs". Confirm?', ref="4", action="close", number="451",
        canonical=CANON, nonce="n1", requested=True,
    )


def _done(nonce: str | None = "n1", status: str = "completed") -> dict:
    return {"type": "response.done", "response": {"status": status, "metadata": {"starlight_readback": nonce} if nonce else None}}


def test_the_answer_window_arms_after_the_read_back_response() -> None:
    box: dict = {"consent": _close_451(), "playback_until": 50.0, "response_audio": 9600, "response_said": CANON}
    x_voice.arm_after_readback(box, now=48.0, event=_done())
    assert box["consent"].armed is True
    assert box["consent"].at == pytest.approx(50.0 + consent.ECHO_TAIL_S)
    x_voice.arm_after_readback(box, now=60.0)
    assert box["consent"].at == pytest.approx(50.0 + consent.ECHO_TAIL_S), "arming happens once"


def test_a_read_back_with_no_audio_drops_the_consent() -> None:
    box: dict = {"consent": _close_451(), "playback_until": 0.0, "response_audio": 0, "response_said": CANON}
    x_voice.arm_after_readback(box, now=10.0, event=_done())
    assert box["consent"] is None


def test_a_cancelled_read_back_drops_the_consent() -> None:
    box: dict = {"consent": _close_451(), "response_audio": 4800, "response_said": CANON}
    x_voice.arm_after_readback(box, now=10.0, event=_done(status="cancelled"))
    assert box["consent"] is None


def test_cards_are_looked_for_in_the_order_they_move() -> None:
    from starlight_voice import approval

    assert approval._FLOW == ("inbox", "running", "reports", "failed", "archive")


def test_the_model_never_receives_the_triage_token() -> None:
    box: dict = {}
    asked = {"ok": True, "started": False, "readback": "Close demo 4. Confirm?", "token": "abc123", "ref": "4", "action": "close", "repo": "demo", "number": "451"}
    out = x_voice.open_triage_consent(box, asked)
    assert "abc123" not in json.dumps(out)
    assert box["consent"].canonical == CANON
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


def test_an_answer_that_names_another_action_or_item_is_a_no() -> None:
    pending = _close_451()
    pending.arm(playback_until=0.0, now=0.0)
    def hear(line):
        return consent.answer(pending, line, spoke_at=pending.at + 1, now=pending.at + 2)
    assert hear("yes keep number 5") == "no", "Codex P1: a different instruction is not a yes"
    assert hear("yes review") == "no"
    assert hear("yes 7") == "no"
    assert hear("yes close") == "yes"
    assert hear("yes 451") == "yes"
    assert hear("yes number 4") == "yes", "the queue ref Frank heard earlier still names it"


def test_only_the_scripted_read_back_spoken_word_for_word_arms() -> None:
    box: dict = {"consent": _close_451(), "playback_until": 50.0, "response_audio": 9600, "response_said": CANON}
    x_voice.arm_after_readback(box, now=48.0, event=_done(nonce=None))
    assert box["consent"].armed is False, "a response the listener did not request cannot arm, even with the right words"
    x_voice.arm_after_readback(box, now=48.0, event=_done(nonce="other"))
    assert box["consent"].armed is False
    box["response_said"] = "Do not close demo pull request 451. Shall I keep it?"
    x_voice.arm_after_readback(box, now=48.0, event=_done())
    assert box["consent"] is None, "Codex round 2 P1: a negated or reworded read-back drops the decision"


def test_the_bridge_hands_over_the_pr_number_from_the_key(monkeypatch, tmp_path) -> None:
    script = tmp_path / "bin" / "triage.mjs"
    script.parent.mkdir()
    script.write_text("")
    monkeypatch.setenv("STARLIGHT_TRIAGE_BIN", str(script))
    out = json.dumps({"pending": True, "readback": "Close demo 451. Confirm?", "token": "abc123", "key": "frankxai/demo#451@" + "a" * 40})
    asked = triage_bridge.ask("4", "close", lambda cmd: types.SimpleNamespace(returncode=0, stdout=out))
    assert asked["number"] == "451"
    box: dict = {}
    x_voice.open_triage_consent(box, asked)
    assert box["consent"].number == "451"


def test_settling_consent_logs_with_the_real_event_writer(monkeypatch, tmp_path) -> None:
    """Codex P1: append_event took kind twice and crashed the pump after the decision was written."""
    from starlight_voice import session_store

    monkeypatch.setattr(session_store, "sessions_path", lambda: tmp_path / "sessions.jsonl")
    monkeypatch.setattr(x_voice.triage_bridge, "record", lambda *a: {"ok": True, "spoken": "Done."})
    monkeypatch.setattr(x_voice, "_touch_live", lambda *a, **k: None)
    now = time.monotonic()
    pending = _armed(now)
    pending.ref, pending.action, pending.token = "4", "close", "abc123"
    ws = _Socket()
    asyncio.run(x_voice._hear(ws, {"consent": pending, "session_id": "s1"}, "yes", spoke_at=now + 0.5))
    assert ws.sent[1] == {"type": "response.create"}
    logged = [json.loads(line) for line in (tmp_path / "sessions.jsonl").read_text().splitlines()]
    assert logged[-1]["kind"] == "consent" and logged[-1]["consent_kind"] == "triage"


def test_the_listener_asks_for_the_read_back_itself() -> None:
    box: dict = {"consent": consent.Pending(kind="triage", readback="r", canonical=CANON)}
    request = x_voice.readback_request(box)
    assert request["response"]["instructions"].endswith(CANON)
    assert request["response"]["metadata"]["starlight_readback"] == box["consent"].nonce
    assert x_voice.readback_request(box) == {"type": "response.create"}, "asked once"
    assert x_voice.readback_request({}) == {"type": "response.create"}


def test_the_first_utterance_after_arming_decides_and_a_change_of_topic_drops_it() -> None:
    pending = _close_451()
    pending.arm(playback_until=0.0, now=0.0)
    pending.answer_item = "item_7"
    t = pending.at + 1
    assert consent.answer(pending, "yes", spoke_at=t, item="item_9", now=t) is None, "not the utterance that answers"
    assert consent.answer(pending, "what's the weather like", spoke_at=t, item="item_7", now=t) == "dropped"


def test_a_dropped_consent_records_nothing(monkeypatch) -> None:
    monkeypatch.setattr(x_voice.triage_bridge, "record", lambda *a: pytest.fail("a change of topic is not a yes"))
    _quiet(monkeypatch)
    now = time.monotonic()
    pending = _armed(now)
    box: dict = {"consent": pending}
    ws = _Socket()
    asyncio.run(x_voice._hear(ws, box, "tell me the weather", spoke_at=now + 0.5))
    assert box["consent"] is None
    assert "dropped" in ws.sent[0]["item"]["content"][0]["text"]


def test_speech_timing_comes_from_the_microphone_clock_not_event_arrival() -> None:
    box: dict = {}
    for i in range(10):  # ten 100 ms chunks captured at t = 100.1 ... 101.0, sent promptly
        x_voice.note_mic(box, pcm_bytes=4800, captured=100.1 + i * 0.1, sent_at=100.12 + i * 0.1)
    assert x_voice.capture_time(box, 250) == pytest.approx(100.25)
    assert x_voice.capture_time(box, None) is None, "no offset, no claim about who spoke"
    assert x_voice.capture_time(box, 5000) is None, "an offset this session never sent"
    box["playback_until"] = 100.5
    # A late event for audio captured during Ara's playback stays ineligible whenever it arrives.
    assert x_voice.eligible_start(box, x_voice.capture_time(box, 600)) is None
    assert x_voice.eligible_start(box, None) is None


def test_a_chunk_that_waited_in_the_queue_has_no_trusted_time() -> None:
    box: dict = {}
    x_voice.note_mic(box, pcm_bytes=4800, captured=100.0, sent_at=110.0)  # Codex round 3 P1: sent 10 s late
    assert x_voice.capture_time(box, 50) is None


def test_speaking_over_the_read_back_drops_the_decision() -> None:
    pending = _close_451()
    box: dict = {"consent": pending}
    assert x_voice.claim_answer(box, "item_1", start=None) is True, "spoke while it was still being read"
    assert box["consent"] is None
    pending = _close_451()
    pending.arm(playback_until=100.0, now=90.0)
    box = {"consent": pending}
    assert x_voice.claim_answer(box, "item_2", start=95.0) is True, "Codex round 3 P1: interrupted before playback ended"
    assert box["consent"] is None
    pending = _close_451()
    pending.arm(playback_until=100.0, now=90.0)
    box = {"consent": pending}
    assert x_voice.claim_answer(box, "item_3", start=pending.at + 0.5) is False
    assert pending.answer_item == "item_3"
    assert x_voice.claim_answer(box, "item_4", start=pending.at + 2) is False, "only the first utterance answers"
    assert pending.answer_item == "item_3"
