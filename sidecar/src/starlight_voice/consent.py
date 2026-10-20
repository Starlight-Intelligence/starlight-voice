"""Consent comes from Frank's transcribed words, never from the model.

A read-back opens one pending consent. It arms only after Ara has finished speaking the read-back,
and only speech that began after that, outside her own playback and its echo tail, can answer it.
The answer must be short and explicit. The model sees the read-back but never the token, so it
cannot confirm on its own, and a tool result telling it to "confirm now" has nothing to confirm with.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

WINDOW_S = 45.0
# Speech starting this soon after Ara's audio stops may be her own voice in the room.
ECHO_TAIL_S = 1.5
MAX_ANSWER_WORDS = 5

_DENY = re.compile(
    r"\b(no|nope|nah|don'?t|do not|stop|cancel|wait|hold on|not|never mind|nein|nee|niet|warte|abbrechen|stopp)\b",
    re.IGNORECASE,
)
# The answer has to open with the yes, and every word in it has to belong to a confirmation.
# "Okay, where is my phone" and "yes, later" are not answers.
_OPENERS = (
    "yes", "yeah", "yep", "yup", "sure", "correct", "okay", "ok", "affirmative",
    "do it", "go ahead", "that's right", "thats right",
    "ja", "jawohl", "genau", "klopt", "doe maar",
)
_CONFIRMING = frozenset(
    "yes yeah yep yup sure correct okay ok affirmative do it go ahead that's thats right please "
    "close keep review hand off handoff confirm confirmed that one this number "
    "ja jawohl genau bitte klopt doe maar prima mach es".split()
)
_WORDS = re.compile(r"[a-z0-9']+")


def _words(line: str) -> list[str]:
    return _WORDS.findall(str(line).lower())


def verdict(line: str) -> str | None:
    words = _words(line)
    if not words:
        return None
    text = " ".join(words)
    if _DENY.search(text):
        return "no"
    if len(words) > MAX_ANSWER_WORDS:
        return None
    opens = any(text == opener or text.startswith(opener + " ") for opener in _OPENERS)
    if opens and all(word in _CONFIRMING or word.isdigit() for word in words):
        return "yes"
    return None


@dataclass
class Pending:
    kind: str  # "triage" or "handoff"
    readback: str
    token: str = ""
    ref: str = ""
    action: str = ""
    proposal: str = ""
    armed: bool = False
    at: float = 0.0  # when the read-back finished playing, plus the echo tail
    opened: float = 0.0

    def __post_init__(self) -> None:
        if not self.opened:
            self.opened = time.monotonic()

    def arm(self, playback_until: float, now: float | None = None) -> None:
        clock = now if now is not None else time.monotonic()
        self.at = max(clock, playback_until) + ECHO_TAIL_S
        self.armed = True

    def expired(self, now: float | None = None) -> bool:
        clock = now if now is not None else time.monotonic()
        start = self.at if self.armed else self.opened
        return clock - start > WINDOW_S


def answer(pending: Pending | None, line: str, *, spoke_at: float | None, now: float | None = None) -> str | None:
    """'yes' or 'no' when this transcript settles the pending consent, 'expired', or None to keep waiting.

    ``spoke_at`` is when Frank began the utterance, or None when it began during Ara's playback.
    Speech that began before the read-back finished never answers it.
    """

    if pending is None:
        return None
    if pending.expired(now):
        return "expired"
    if not pending.armed or spoke_at is None or spoke_at <= pending.at:
        return None
    return verdict(line)
