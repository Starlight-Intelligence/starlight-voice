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
_WORDS = re.compile(r"[^\W_]+(?:'[^\W_]+)?")


def _words(line: str) -> list[str]:
    return _WORDS.findall(str(line).lower())


# An action word in an answer has to name the action that was read back. "Yes, keep 5" is not a yes
# to "close 4": it is a different instruction, so it settles nothing and is treated as a no.
_ACTION_WORDS = {
    "close": "close", "keep": "keep", "review": "review", "handoff": "handoff", "hand": "handoff",
}


def _numbers(text: str) -> set[str]:
    return set(re.findall(r"\d+", str(text)))


def verdict(line: str, *, action: str = "", allowed_numbers: frozenset[str] | set[str] = frozenset()) -> str | None:
    words = _words(line)
    if not words:
        return None
    text = " ".join(words)
    if _DENY.search(text):
        return "no"
    if len(words) > MAX_ANSWER_WORDS:
        return None
    named = {_ACTION_WORDS[w] for w in words if w in _ACTION_WORDS}
    if named - {action}:
        return "no"
    if any(w.isdigit() and w not in allowed_numbers for w in words):
        return "no"
    opens = any(text == opener or text.startswith(opener + " ") for opener in _OPENERS)
    if opens and all(word in _CONFIRMING or word.isdigit() for word in words):
        return "yes"
    return None


def spoke_canonical(pending: Pending, said: str) -> bool:
    """Ara said the listener's read-back sentence word for word, so Frank heard exactly that decision."""

    return bool(pending.canonical) and _words(said) == _words(pending.canonical)


@dataclass
class Pending:
    kind: str  # "triage" or "handoff"
    readback: str
    token: str = ""
    ref: str = ""
    action: str = ""
    number: str = ""  # the PR number the read-back names
    # The exact sentence the listener asks Ara to say. Only a response the listener requested, tagged
    # with this nonce and spoken word for word, arms the consent.
    canonical: str = ""
    nonce: str = ""
    requested: bool = False
    # The first utterance Frank starts after arming. It alone decides: yes, no, or anything else drops it.
    answer_item: str = ""
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


def answer(
    pending: Pending | None, line: str, *, spoke_at: float | None, item: str = "", now: float | None = None,
) -> str | None:
    """'yes', 'no', 'dropped' or 'expired' when this transcript settles the pending consent; None to keep waiting.

    ``spoke_at`` is when Frank began the utterance by the microphone's clock, or None when that is
    unknown or fell inside Ara's playback. Speech that began before the read-back finished never
    answers it. The first utterance after arming decides: anything that is not a clear yes or no
    drops the consent, so a "yes" to some later question can never reach an old decision.
    """

    if pending is None:
        return None
    if pending.expired(now):
        return "expired"
    if not pending.armed or spoke_at is None or spoke_at <= pending.at:
        return None
    if pending.answer_item and item and item != pending.answer_item:
        return None
    action = pending.action or ("handoff" if pending.kind == "handoff" else "")
    allowed = {pending.ref, pending.number} - {""} if pending.kind == "triage" else _numbers(pending.readback)
    return verdict(line, action=action, allowed_numbers=allowed) or "dropped"
