from starlight_voice.status import system_status


def test_system_status_shape() -> None:
    s = system_status()
    for key in ("settings", "adapters", "variants", "runs", "gateway", "first_audio_budget_ms"):
        assert key in s
    assert s["settings"]["stt_engine"] == "groq-openrouter"
    # variants metadata present without constructing services (light path)
    keys = {v["key"] for v in s["variants"]}
    assert keys == {"component", "openai-realtime", "gemini-live", "x-voice"}
    assert s["gateway"] in {"running", "not running"}
    assert isinstance(s["runs"], list)
