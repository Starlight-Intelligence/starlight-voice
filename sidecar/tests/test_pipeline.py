from starlight_voice import adapters
from starlight_voice.pipeline import AgentPipeline


def test_health_declares_current_capabilities() -> None:
    health = AgentPipeline().health()
    avail = adapters.availability()
    # voice_loop must mirror the real cloud-lane availability (pipecat + openrouter + a
    # TTS + a groq STT lane), so the assertion holds whether or not the voice extra is
    # installed — not a hardcoded snapshot that rots the moment the extra lands.
    expected_voice = bool(
        avail.get("pipecat", False)
        and avail.get("openrouter", False)
        and (avail.get("elevenlabs", False) or avail.get("cartesia", False))
        and (avail.get("groq-openrouter", False) or avail.get("groq", False))
    )

    assert health["status"] == "ok"
    assert health["capabilities"]["text_mode"] is True
    assert health["capabilities"]["voice_loop"] == expected_voice


def test_default_text_path_is_alive() -> None:
    result = AgentPipeline().process_text("hello")

    assert result["route"]["tier"] == "tier1-fast"
    assert result["response"]["type"] == "voice"
