"""Local activation gates for the voice loop (claps, hotkey, wake-word).

These run OUTSIDE the cloud loop: a cheap, always-on, on-device listener that decides
WHEN to arm a turn, so the mic audio never egresses to Groq/OpenRouter until the user
actually summons Starlight. Claps need no API key and no model — pure energy onset
detection — which is why they are the runnable-today activation tier.
"""

from __future__ import annotations

from .clap import ClapDetector, MultiClapTrigger, detect_clap_onsets

__all__ = ["ClapDetector", "MultiClapTrigger", "detect_clap_onsets"]
