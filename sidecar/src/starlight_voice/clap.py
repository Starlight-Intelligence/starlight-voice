"""Double-clap gate for the voice operator.

One knock stays shut. Speech stays shut, because a voice is a long loud run.
A clap is a loud run that ends inside ``max_burst_ms``. Two of those, between
``min_gap_ms`` and ``max_gap_ms``, emit ``toggle``.
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
        threshold: float = 0.18,
        max_burst_ms: int = 80,
        min_gap_ms: int = 160,
        max_gap_ms: int = 700,
        refractory_ms: int = 1000,
    ) -> None:
        if sample_rate <= 0 or hop_ms <= 0:
            raise ValueError("sample_rate and hop_ms must be positive")
        if not (0 < min_gap_ms < max_gap_ms):
            raise ValueError("clap gap window is inverted")
        self.sample_rate = sample_rate
        self.hop_ms = hop_ms
        self.threshold = threshold
        self.max_burst_hops = max(1, max_burst_ms // hop_ms)
        self.min_gap_ms = min_gap_ms
        self.max_gap_ms = max_gap_ms
        self.refractory_hops = max(1, refractory_ms // hop_ms)
        self._hop_samples = max(1, int(sample_rate * hop_ms / 1000))
        self._pending: list[float] = []
        self._hop = 0
        self._mode = "idle"
        self._attack_hops = 0
        self._last_clap: int | None = None
        self._cool_until = 0

    def feed(self, samples: Sequence[float]) -> list[str]:
        self._pending.extend(float(sample) for sample in samples)
        events: list[str] = []
        while len(self._pending) >= self._hop_samples:
            hop = self._pending[: self._hop_samples]
            del self._pending[: self._hop_samples]
            event = self._push_rms(rms(hop))
            if event:
                events.append(event)
        return events

    def _push_rms(self, level: float) -> str | None:
        self._hop += 1
        if self._hop < self._cool_until:
            self._mode = "idle"
            self._attack_hops = 0
            return None
        hot = level >= self.threshold
        if self._mode == "idle":
            if hot:
                self._mode = "attack"
                self._attack_hops = 1
            return None
        if self._mode == "attack":
            if hot:
                self._attack_hops += 1
                if self._attack_hops > self.max_burst_hops:
                    self._mode = "reject"
                return None
            self._mode = "idle"
            self._attack_hops = 0
            return self._note_clap()
        if not hot:
            self._mode = "idle"
        return None

    def _note_clap(self) -> str | None:
        if self._last_clap is None:
            self._last_clap = self._hop
            return None
        gap_ms = (self._hop - self._last_clap) * self.hop_ms
        if gap_ms < self.min_gap_ms:
            return None
        if gap_ms <= self.max_gap_ms:
            self._last_clap = None
            self._cool_until = self._hop + self.refractory_hops
            return "toggle"
        self._last_clap = self._hop
        return None
