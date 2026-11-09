"""Headless construction test for the cloud voice graph.

Skips when the `voice` extra or provider keys are absent (e.g. CI), so it stays green there
while validating real graph assembly locally. Does NOT run audio; see benchmarks for latency.
"""

import os

import pytest

pytest.importorskip("pipecat", reason="voice extra not installed")

_KEYS = ("OPENROUTER_API_KEY", "GROQ_API_KEY", "ELEVENLABS_API_KEY")


@pytest.mark.skipif(
    not all(os.environ.get(k) for k in _KEYS),
    reason="cloud provider keys not set",
)
def test_selftest_assembles_cloud_graph() -> None:
    from starlight_voice.voice_loop import selftest

    result = selftest()
    assert result["ok"] is True
    # 7-proc chain (stt, router, tool lifecycle, user, llm, tts, assistant) + Source+Sink = 9
    assert result["pipeline_processors"] == 9
    assert result["stt"] == "groq-openrouter"
    assert result["tts"] == "elevenlabs"


@pytest.mark.skipif(
    not all(os.environ.get(k) for k in _KEYS),
    reason="cloud provider keys not set",
)
def test_provider_pin_lands_on_the_service_not_just_settings_string() -> None:
    # review wf_a9484479: the old test asserted a string built from Settings, masking that the
    # pin was dropped on the wire. Assert the ACTUAL constructed service carries the provider block.
    from starlight_voice.config import Settings
    from starlight_voice.voice_loop import _llm_service

    svc = _llm_service(Settings.from_env())
    extra = (getattr(svc._settings, "extra", {}) or {}).get("extra_body", {})
    assert extra.get("provider", {}).get("order") == ["cerebras"]
    assert extra["provider"]["allow_fallbacks"] is False  # no transcript egress on near-miss
