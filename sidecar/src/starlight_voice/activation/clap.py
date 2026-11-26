"""Clap activation — summon Starlight by clapping. No key, no model, no cloud.

A clap is a short, broadband, high-energy transient: a sharp rise in block RMS far above
the rolling noise floor, followed by a fast decay. We detect onsets against an adaptive
floor (EMA of quiet blocks), debounce with a refractory window, then a `MultiClapTrigger`
fires only when N onsets land inside a time window with sane inter-clap spacing — so a
single door-slam or a steady-loud room does NOT trigger, but a deliberate double-clap does.

The detection core (`ClapDetector`, `MultiClapTrigger`) is pure and deterministic over a
stream of (rms, timestamp) blocks, so it is unit-tested with synthetic audio without a mic.
`listen()` is the only part that touches sounddevice, imported lazily.

CLI:
    python -m starlight_voice.activation.clap                 # print events, tune live
    python -m starlight_voice.activation.clap --claps 2 --on-trigger "starlight-arm"
"""

from __future__ import annotations

import math
import subprocess
import sys
import time
from dataclasses import dataclass, field

# --- tunables (conservative defaults; override via CLI) ---------------------------------
DEFAULT_SR = 16_000
DEFAULT_BLOCK = 512                  # ~32 ms at 16 kHz
RISE_RATIO = 4.5                     # onset when block RMS > floor * RISE_RATIO ...
ABS_MIN_RMS = 0.06                   # ... AND above this absolute floor (ignores quiet rooms)
REFRACTORY_S = 0.12                  # min gap between two counted claps (debounce one clap)
FLOOR_ATTACK = 0.05                  # EMA weight when updating the noise floor on quiet blocks
MIN_GAP_S = 0.12                     # inter-clap spacing that counts as a deliberate sequence
MAX_GAP_S = 0.70
WINDOW_S = 1.20                      # all N claps must land within this window


def block_rms(samples) -> float:
    """Root-mean-square of a float block in [-1, 1]. Accepts list or numpy array."""
    n = len(samples)
    if n == 0:
        return 0.0
    try:  # numpy fast path
        import numpy as np

        arr = np.asarray(samples, dtype="float32")
        return float(np.sqrt(np.mean(arr * arr)))
    except Exception:
        return math.sqrt(sum(float(s) * float(s) for s in samples) / n)


@dataclass
class ClapDetector:
    """Adaptive single-clap onset detector over a stream of block RMS values."""

    rise_ratio: float = RISE_RATIO
    abs_min_rms: float = ABS_MIN_RMS
    refractory_s: float = REFRACTORY_S
    floor_attack: float = FLOOR_ATTACK
    _floor: float = field(default=1e-4, init=False)
    _last_onset_t: float = field(default=-1e9, init=False)
    _armed: bool = field(default=True, init=False)

    def feed(self, rms: float, t: float) -> bool:
        """Push one block. Returns True exactly once per clap onset."""
        threshold = max(self._floor * self.rise_ratio, self.abs_min_rms)
        is_loud = rms >= threshold

        onset = False
        if is_loud and self._armed and (t - self._last_onset_t) >= self.refractory_s:
            onset = True
            self._last_onset_t = t
            self._armed = False           # require a quiet block before the next onset (decay)
        elif not is_loud:
            self._armed = True
            # adapt the noise floor only on quiet blocks so claps don't inflate it
            self._floor = (1 - self.floor_attack) * self._floor + self.floor_attack * rms

        return onset


@dataclass
class MultiClapTrigger:
    """Fires when `count` clap onsets land within `window_s` at deliberate spacing."""

    count: int = 2
    window_s: float = WINDOW_S
    min_gap_s: float = MIN_GAP_S
    max_gap_s: float = MAX_GAP_S
    _onsets: list[float] = field(default_factory=list)

    def add_onset(self, t: float) -> bool:
        """Register a clap at time t. Returns True when a valid N-clap pattern completes."""
        if self._onsets:
            gap = t - self._onsets[-1]
            if gap < self.min_gap_s:        # too close: same clap echo, ignore
                return False
            if gap > self.max_gap_s:        # too slow: start a fresh sequence
                self._onsets = []
        self._onsets.append(t)
        self._onsets = [x for x in self._onsets if t - x <= self.window_s]
        if len(self._onsets) >= self.count:
            self._onsets = []
            return True
        return False


def detect_clap_onsets(rms_stream) -> list[float]:
    """Pure helper for tests: feed an iterable of (rms, t) -> list of onset timestamps."""
    det = ClapDetector()
    return [t for (rms, t) in rms_stream if det.feed(rms, t)]


def listen(count: int = 2, on_trigger: str | None = None, sr: int = DEFAULT_SR,
           block: int = DEFAULT_BLOCK, verbose: bool = True) -> int:
    """Always-on mic listener. On an N-clap pattern, run `on_trigger` (shell) or print."""
    try:
        import numpy as np  # noqa: F401
        import sounddevice as sd
    except Exception as exc:  # pragma: no cover - env-dependent
        print(f'{{"error":"clap listener needs sounddevice+numpy: {exc}"}}', file=sys.stderr)
        return 2

    det = ClapDetector()
    trig = MultiClapTrigger(count=count)
    t0 = time.monotonic()

    def fire() -> None:
        ts = round(time.monotonic() - t0, 3)
        print(f'{{"event":"clap-trigger","claps":{count},"t":{ts}}}', flush=True)
        if on_trigger:
            try:
                subprocess.Popen(on_trigger, shell=True)
            except Exception as exc:  # pragma: no cover
                print(f'{{"error":"on_trigger failed: {exc}"}}', file=sys.stderr, flush=True)

    def callback(indata, frames, time_info, status):  # pragma: no cover - audio thread
        t = time.monotonic() - t0
        rms = block_rms(indata[:, 0])
        if det.feed(rms, t):
            if verbose:
                print(f'{{"event":"clap","t":{round(t, 3)},"rms":{round(rms, 4)}}}', flush=True)
            if trig.add_onset(t):
                fire()

    if verbose:
        print(f'{{"event":"listening","claps_to_trigger":{count}}}', flush=True)
    with sd.InputStream(channels=1, samplerate=sr, blocksize=block, dtype="float32",
                        callback=callback):
        try:
            while True:
                sd.sleep(250)
        except KeyboardInterrupt:
            return 0


def _main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(prog="starlight-voice clap")
    p.add_argument("--claps", type=int, default=2, help="claps in the window to trigger")
    p.add_argument("--on-trigger", default=None, help="shell command to run on trigger")
    p.add_argument("--quiet", action="store_true", help="only print the trigger event")
    args = p.parse_args(argv)
    return listen(count=args.claps, on_trigger=args.on_trigger, verbose=not args.quiet)


if __name__ == "__main__":
    raise SystemExit(_main())
