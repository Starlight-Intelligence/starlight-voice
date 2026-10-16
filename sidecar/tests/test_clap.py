from starlight_voice.clap import ClapDetector


def _hops(detector: ClapDetector, level: float, count: int) -> list[str]:
    frame = [level] * detector._hop_samples
    events: list[str] = []
    for _ in range(count):
        events.extend(detector.feed(frame))
    return events


def _clap(detector: ClapDetector) -> list[str]:
    return _hops(detector, 0.9, 2) + _hops(detector, 0.0, 1)


def test_two_claps_toggle() -> None:
    detector = ClapDetector(sample_rate=16_000)
    events = _clap(detector)
    events += _hops(detector, 0.0, 30)
    events += _clap(detector)
    assert events == ["toggle"]


def test_one_clap_stays_shut() -> None:
    detector = ClapDetector(sample_rate=16_000)
    assert _clap(detector) == []


def test_claps_two_seconds_apart_do_not_toggle() -> None:
    detector = ClapDetector(sample_rate=16_000)
    events = _clap(detector)
    events += _hops(detector, 0.0, 200)
    events += _clap(detector)
    assert events == []


def test_speech_is_not_a_clap() -> None:
    detector = ClapDetector(sample_rate=16_000)
    events = _hops(detector, 0.5, 20)
    events += _hops(detector, 0.0, 10)
    assert events == []


def test_refractory_ignores_the_echo() -> None:
    detector = ClapDetector(sample_rate=16_000)
    events = _clap(detector)
    events += _hops(detector, 0.0, 30)
    events += _clap(detector)
    events += _hops(detector, 0.0, 20)
    events += _clap(detector)
    events += _hops(detector, 0.0, 30)
    events += _clap(detector)
    assert events == ["toggle"]
