"""Architecture bake-off assembly tests.

Skips without the voice extra / keys (CI-safe). Validates that the harness can swap engine
blocks: component (A) and OpenAI Realtime S2S (B) both assemble; Gemini (C) is honestly
reported as blocked rather than silently passing.
"""

import os

import pytest

pytest.importorskip("pipecat", reason="voice extra not installed")

from starlight_voice.voice_engines import VARIANTS, selftest_variant


def test_variant_registry_covers_the_three_architectures() -> None:
    assert set(VARIANTS) == {"component", "openai-realtime", "gemini-live", "x-voice"}


@pytest.mark.skipif(not os.environ.get("OPENROUTER_API_KEY"), reason="no OpenRouter key")
def test_component_variant_assembles() -> None:
    r = selftest_variant("component")
    assert r["assembles"] is True
    assert r["processors"] == 9  # stt, router, memory, user, llm, tts, assistant + Source/Sink


@pytest.mark.skipif(not os.environ.get("OPENAI_API_KEY"), reason="no OpenAI key")
def test_openai_realtime_variant_assembles() -> None:
    r = selftest_variant("openai-realtime")
    assert r["assembles"] is True  # S2S engine block swaps in cleanly


def test_selftest_reports_blocked_variant_honestly() -> None:
    # Gemini live isn't installed -> must report assembles=False with an error, not crash
    r = selftest_variant("gemini-live")
    assert r["assembles"] is False
    assert "error" in r
