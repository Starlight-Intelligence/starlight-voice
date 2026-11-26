"""Deterministic clap-detection tests over synthetic block-RMS streams (no microphone)."""

from __future__ import annotations

from starlight_voice.activation.clap import (
    ClapDetector,
    MultiClapTrigger,
    detect_clap_onsets,
)

DT = 512 / 16_000  # ~0.032s per block, matching the live blocksize
QUIET = 0.004
CLAP = 0.55


def _stream(clap_times, duration=2.0):
    """Yield (rms, t) blocks: QUIET everywhere except a one-block spike at each clap time."""
    n = int(duration / DT)
    spikes = {int(round(ct / DT)) for ct in clap_times}
    for i in range(n):
        yield (CLAP if i in spikes else QUIET, round(i * DT, 4))


def test_single_clap_is_one_onset():
    onsets = detect_clap_onsets(_stream([0.5]))
    assert len(onsets) == 1


def test_double_clap_triggers():
    det, trig = ClapDetector(), MultiClapTrigger(count=2)
    fired = 0
    for rms, t in _stream([0.4, 0.7]):       # ~0.3s apart, deliberate
        if det.feed(rms, t) and trig.add_onset(t):
            fired += 1
    assert fired == 1


def test_door_slam_does_not_trigger_double():
    """A single loud transient must not fire a two-clap trigger."""
    det, trig = ClapDetector(), MultiClapTrigger(count=2)
    fired = sum(
        1 for rms, t in _stream([0.5]) if det.feed(rms, t) and trig.add_onset(t)
    )
    assert fired == 0


def test_too_fast_double_is_debounced():
    """Two spikes inside the refractory window count as one clap, not two."""
    det, trig = ClapDetector(), MultiClapTrigger(count=2)
    fired = sum(
        1
        for rms, t in _stream([0.40, 0.44])   # 40ms apart < refractory/min_gap
        if det.feed(rms, t) and trig.add_onset(t)
    )
    assert fired == 0


def test_steady_loud_room_does_not_spam():
    """Constant loud input fires at most one onset, then disarms until it goes quiet."""
    det = ClapDetector()
    onsets = sum(1 for i in range(60) if det.feed(CLAP, round(i * DT, 4)))
    assert onsets <= 1
