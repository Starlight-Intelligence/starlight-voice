from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sidecar" / "src"))

from starlight_voice import adapters
from starlight_voice.browser import BrowserAutomationAdapter
from starlight_voice.cognition import CognitionRouter
from starlight_voice.config import Settings


def measure_router(n: int) -> list[int]:
    router = CognitionRouter()
    samples: list[int] = []
    for _ in range(n):
        started = perf_counter()
        router.decide("open browser and search the docs")
        samples.append(int((perf_counter() - started) * 1000))
    return samples


def measure_browser_dry_run(n: int) -> list[int]:
    adapter = BrowserAutomationAdapter()
    samples: list[int] = []
    for _ in range(n):
        started = perf_counter()
        adapter.run("open the docs")
        samples.append(int((perf_counter() - started) * 1000))
    return samples


def first_audio_readiness() -> dict[str, object]:
    """Gate for the e2e first-audio probe.

    The load-bearing P1 deliverable is measuring mic-close -> first PLAYABLE TTS chunk on
    THIS GTX 1650, not assuming the ~800ms budget. Until the selected STT/LLM/TTS engines
    are installed and `voice_loop.py` is wired, this reports the budget + exactly what is
    missing instead of fabricating a latency number. When the loop lands, replace the
    `ready=False` branch with the real timed run (measure to playable audio, PAST WAV/Ogg/ID3
    container headers — those falsely report ~50ms).
    """
    settings = Settings.from_env()
    avail = adapters.availability()
    needed = {
        "stt": settings.stt_engine,
        "llm": "openrouter",
        "tts": settings.tts_engine,
        "framework": "pipecat",
    }
    missing = [f"{stage}:{eng}" for stage, eng in needed.items() if not avail.get(eng, False)]
    return {
        "ready": not missing,
        "selected": needed,
        "missing": missing,
        "p50_budget_ms": settings.first_audio_p50_budget_ms,
        "p95_budget_ms": settings.first_audio_p95_budget_ms,
        "install_hint": adapters.INSTALL_HINT,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--probe",
        choices=["router", "browser-dry-run", "first-audio"],
        default="router",
    )
    parser.add_argument("--n", type=int, default=50)
    args = parser.parse_args()

    if args.probe == "first-audio":
        status = first_audio_readiness()
        if not status["ready"]:
            print(
                f"probe=first-audio GATED missing={','.join(status['missing']) or 'none'} "
                f"p50_budget_ms={status['p50_budget_ms']} p95_budget_ms={status['p95_budget_ms']} "
                f"hint='{status['install_hint']}'"
            )
            return 0  # gated is not a failure; it is the honest pre-install state
        # Graph assembles? (construction gate). Real first-audio latency still needs a live
        # mic run on-device — `python -m starlight_voice voice --run` — measured to PLAYABLE audio.
        from starlight_voice.voice_loop import selftest

        st = selftest()
        print(
            f"probe=first-audio READY graph_ok={st['ok']} processors={st['pipeline_processors']} "
            f"stt={st['stt']} llm={st['llm']} tts={st['tts']} "
            f"p50_budget_ms={status['p50_budget_ms']} "
            f"(live latency: run `python -m starlight_voice voice --run` on-device)"
        )
        return 0

    samples = measure_router(args.n) if args.probe == "router" else measure_browser_dry_run(args.n)
    p50 = int(statistics.median(samples))
    p95 = int(sorted(samples)[max(0, int(len(samples) * 0.95) - 1)])
    print(f"probe={args.probe} n={args.n} p50_ms={p50} p95_ms={p95} max_ms={max(samples)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
