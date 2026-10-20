"""Consent comes from Frank's transcribed words, never from the model.

A read-back opens one pending consent. Only an input transcript (his microphone, transcribed by the
socket) can answer it. The model sees the read-back but never the token, so it cannot confirm on its
own, and a tool result telling it to "confirm now" has nothing to confirm with.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

WINDOW_S = 45.0

# A refusal anywhere wins over an affirmative ("no, wait, yes" is not a clear yes).
_DENY = re.compile(
    r"\b(no|nope|nah|don'?t|do not|stop|cancel|wait|hold on|not that|never mind|nein|nee|niet|warte|abbrechen|stopp)\b",
    re.IGNORECASE,
)
# "confirm" is left out on purpose: read-backs end with "Confirm?", and a speaker echo of that must not count.
_AFFIRM = re.compile(
    r"\b(yes|yeah|yep|yup|sure|do it|go ahead|go|correct|that'?s right|okay|ok|ja|jawohl|genau|klar|klopt|doe maar|prima)\b",
    re.IGNORECASE,
)
_WORD = re.compile(r"[a-z0-9']+")
ECHO_OVERLAP = 0.6


def verdict(line: str) -> str | None:
    text = " ".join(str(line).split())
    if not text:
        return None
    if _DENY.search(text):
        return "no"
    if _AFFIRM.search(text):
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
    at: float = field(default_factory=time.monotonic)

    def expired(self, now: float | None = None) -> bool:
        return (now if now is not None else time.monotonic()) - self.at > WINDOW_S


def is_echo(line: str, readback: str) -> bool:
    """The microphone hearing Ara's own read-back is not Frank answering it."""

    heard = set(_WORD.findall(line.lower()))
    said = set(_WORD.findall(readback.lower()))
    return len(heard) >= 3 and len(heard & said) / len(heard) >= ECHO_OVERLAP


def answer(pending: Pending | None, line: str, *, spoke_at: float | None, now: float | None = None) -> str | None:
    """'yes' or 'no' when this transcript settles the pending consent, 'expired', or None to keep waiting.

    ``spoke_at`` is when Frank started the utterance. Speech that began before the read-back (a
    command and a "go" said in one breath) never counts as an answer to it.
    """

    if pending is None:
        return None
    if pending.expired(now):
        return "expired"
    if spoke_at is None or spoke_at <= pending.at:
        return None
    if is_echo(line, pending.readback):
        return None
    return verdict(line)
