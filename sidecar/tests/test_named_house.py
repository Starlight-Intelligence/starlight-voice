"""House naming used by the live face. Imports the shipped function."""

import os

from starlight_voice.session_store import publish, read_live
from starlight_voice.x_voice import _touch_live, face_fields, named_house


def test_one_house_is_selected() -> None:
    assert named_house("Open FrankX") == "frankx"
    assert named_house("Ask GenCreator for the pack") == "gencreator"


def test_no_house_stays_dark() -> None:
    assert named_house("What is the chair doing") == ""


def test_two_houses_stay_dark() -> None:
    assert named_house("FrankX and Arcanea both") == ""


def test_face_fields_keep_started_false() -> None:
    fields = face_fields(
        {"utterance": "Check Starlight"},
        {"primary": "grok-local", "spoken": "Held. I did not start it.", "spawn": True},
    )
    assert fields["house"] == "starlight"
    assert fields["primary"] == "grok-local"
    assert fields["started"] is False
    assert "spawn" not in fields


def test_transcript_touch_keeps_house_and_started(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("STARLIGHT_VOICE_STATE_DIR", str(tmp_path))
    publish("open", pid=os.getpid(), voice="ara", memory="idle")
    box = {"phase": "open", "session_id": "sess-face", "conversation_id": ""}
    _touch_live(
        box,
        **face_fields(
            {"utterance": "Open FrankX"},
            {"primary": "grok-local", "spoken": "Held. I did not start it.", "spawn": True},
        ),
    )
    _touch_live(box, line="you: hello", conversation_id="conv_abcdef")
    live = read_live()
    assert live["phase"] == "open"
    assert live["house"] == "frankx"
    assert live["started"] is False
    assert live["primary"] == "grok-local"
    assert live["line"] == "you: hello"
    assert live["pid"] == os.getpid()
