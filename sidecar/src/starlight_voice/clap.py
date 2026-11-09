"""Double-clap gate for the voice operator.

A clap is a sharp peak, not a loud hop. The Logitech BRIO at desk distance
puts almost all of a clap into a few samples, so an RMS gate of 0.18 never
armed. Speech stays shut because a voice is full across the hop (low crest)
or it keeps going past ``max_burst_ms``.

One clap returns ``clap``. Two, between ``min_gap_ms`` and ``max_gap_ms``,
return ``toggle``.
"""

from __future__ import annotations

import math
from collections.abc import Sequence


def rms(samples: Sequence[float]) -> float:
    count = len(samples)
    if count == 0:
        return 0.0
    total = 0.0
    for sample in samples:
        value = float(sample)
        total += value * value
    return math.sqrt(total / count)


def hop_shape(samples: Sequence[float]) -> tuple[float, float]:
    """Peak absolute level, and crest (peak / RMS). A clap's crest is high."""

    count = len(samples)
    if count == 0:
        return 0.0, 0.0
    total = 0.0
    peak = 0.0
    for sample in samples:
        value = float(sample)
        total += value * value
        mag = abs(value)
        if mag > peak:
            peak = mag
    level = math.sqrt(total / count)
    crest = peak / level if level > 1e-8 else 0.0
    return peak, crest


def resample_linear(samples: Sequence[float], source_rate: int, target_rate: int) -> list[float]:
    if source_rate <= 0 or target_rate <= 0:
        raise ValueError("sample rates must be positive")
    if source_rate == target_rate or len(samples) == 0:
        return [float(sample) for sample in samples]
    target_length = max(1, round(len(samples) * target_rate / source_rate))
    if len(samples) == 1 or target_length == 1:
        return [float(samples[0])]
    scale = (len(samples) - 1) / (target_length - 1)
    last = len(samples) - 1
    output: list[float] = []
    for index in range(target_length):
        position = index * scale
        left = int(position)
        right = min(left + 1, last)
        fraction = position - left
        output.append(float(samples[left]) * (1.0 - fraction) + float(samples[right]) * fraction)
    return output


class ClapDetector:
    def __init__(
        self,
        sample_rate: int = 24_000,
        *,
        hop_ms: int = 10,
        min_peak: float = 0.08,
        min_crest: float = 3.5,
        max_burst_ms: int = 200,
        min_gap_ms: int = 150,
        max_gap_ms: int = 1000,
        refractory_ms: int = 900,
        echo_ratio: float = 0.55,
    ) -> None:
        if sample_rate <= 0 or hop_ms <= 0:
            raise ValueError("sample_rate and hop_ms must be positive")
        if not (0 < min_gap_ms < max_gap_ms):
            raise ValueError("clap gap window is inverted")
        self.sample_rate = sample_rate
        self.hop_ms = hop_ms
        self.min_peak = min_peak
        self.min_crest = min_crest
        self.max_burst_hops = max(1, max_burst_ms // hop_ms)
        self.min_gap_ms = min_gap_ms
        self.max_gap_ms = max_gap_ms
        self.refractory_hops = max(1, refractory_ms // hop_ms)
        self.echo_ratio = echo_ratio
        self._hop_samples = max(1, int(sample_rate * hop_ms / 1000))
        self._pending: list[float] = []
        self._hop = 0
        self._mode = "idle"
        self._attack_hops = 0
        self._burst_peak = 0.0
        self._last_clap: int | None = None
        self._last_peak = 0.0
        self._cool_until = 0
        self._noise = 0.02

    def feed(self, samples: Sequence[float]) -> list[str]:
        self._pending.extend(float(sample) for sample in samples)
        events: list[str] = []
        while len(self._pending) >= self._hop_samples:
            hop = self._pending[: self._hop_samples]
            del self._pending[: self._hop_samples]
            peak, crest = hop_shape(hop)
            event = self._push(peak, crest)
            if event:
                events.append(event)
        return events

    def _floor(self) -> float:
        return max(self.min_peak, self._noise * 7.0)

    def _push(self, peak: float, crest: float) -> str | None:
        self._hop += 1
        if self._hop < self._cool_until:
            self._mode = "idle"
            self._attack_hops = 0
            self._burst_peak = 0.0
            return None
        hot = peak >= self._floor() and crest >= self.min_crest
        if self._mode == "idle":
            if peak < self.min_peak:
                self._noise = (0.95 * self._noise) + (0.05 * peak)
            if hot:
                self._mode = "attack"
                self._attack_hops = 1
                self._burst_peak = peak
            return None
        if self._mode == "attack":
            if hot:
                self._attack_hops += 1
                if peak > self._burst_peak:
                    self._burst_peak = peak
                if self._attack_hops > self.max_burst_hops:
                    self._mode = "reject"
                    self._burst_peak = 0.0
                return None
            self._mode = "idle"
            self._attack_hops = 0
            return self._note_clap(self._burst_peak)
        if not hot:
            self._mode = "idle"
        return None

    def _arm_first(self, peak: float) -> str:
        self._last_clap = self._hop
        self._last_peak = peak
        # Short cool so the speaker tick cannot become the second clap.
        self._cool_until = self._hop + max(1, 80 // self.hop_ms)
        return "clap"

    def _note_clap(self, peak: float) -> str | None:
        if peak <= 0.0:
            return None
        if self._last_clap is None:
            return self._arm_first(peak)
        gap_ms = (self._hop - self._last_clap) * self.hop_ms
        if gap_ms < self.min_gap_ms:
            return None
        if gap_ms > self.max_gap_ms:
            return self._arm_first(peak)
        if peak < self._last_peak * self.echo_ratio:
            return None
        self._last_clap = None
        self._last_peak = 0.0
        self._cool_until = self._hop + self.refractory_hops
        return "toggle"
