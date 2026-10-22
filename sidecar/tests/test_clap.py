from starlight_voice.clap import ClapDetector


def _hops(detector: ClapDetector, level: float, count: int) -> list[str]:
    frame = [level] * detector._hop_samples
    events: list[str] = []
    for _ in range(count):
        events.extend(detector.feed(frame))
    return events


def _clap(detector: ClapDetector, peak: float = 0.9) -> list[str]:
    frame = [0.0] * detector._hop_samples
    frame[0] = peak
    frame[1] = peak * 0.65
    return detector.feed(frame) + _hops(detector, 0.0, 1)


def test_two_claps_toggle() -> None:
    detector = ClapDetector(sample_rate=16_000)
    events = _clap(detector)
    events += _hops(detector, 0.0, 30)
    events += _clap(detector)
    assert events == ["clap", "toggle"]


def test_one_clap_stays_shut() -> None:
    detector = ClapDetector(sample_rate=16_000)
    assert _clap(detector) == ["clap"]


def test_claps_two_seconds_apart_do_not_toggle() -> None:
    detector = ClapDetector(sample_rate=16_000)
    events = _clap(detector)
    events += _hops(detector, 0.0, 200)
    events += _clap(detector)
    assert events == ["clap", "clap"]


def test_speech_is_not_a_clap() -> None:
    detector = ClapDetector(sample_rate=16_000)
    events = _hops(detector, 0.5, 20)
    events += _hops(detector, 0.0, 10)
    assert events == []


def test_quiet_desk_double_clap_toggles() -> None:
    """A BRIO clap is a few loud samples inside a quiet hop. RMS 0.18 missed it."""
    detector = ClapDetector(sample_rate=16_000)
    frame = [0.012] * detector._hop_samples
    frame[0] = 0.22
    frame[1] = 0.16
    frame[2] = 0.08
    events = detector.feed(frame)
    events += _hops(detector, 0.012, 2)
    events += _hops(detector, 0.012, 40)
    events += detector.feed(frame)
    events += _hops(detector, 0.012, 2)
    assert events == ["clap", "toggle"]


def test_quiet_echo_does_not_open() -> None:
    detector = ClapDetector(sample_rate=16_000)
    events = _clap(detector, 0.9)
    events += _hops(detector, 0.0, 30)
    events += _clap(detector, 0.2)
    assert events == ["clap"]


def test_refractory_ignores_the_echo() -> None:
    detector = ClapDetector(sample_rate=16_000)
    events = _clap(detector)
    events += _hops(detector, 0.0, 30)
    events += _clap(detector)
    events += _hops(detector, 0.0, 20)
    events += _clap(detector)
    events += _hops(detector, 0.0, 30)
    events += _clap(detector)
    assert events == ["clap", "toggle"]
