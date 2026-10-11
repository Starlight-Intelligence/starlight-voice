"""Provider factories must never silently send audio to a different engine."""

import sys
from types import SimpleNamespace

import pytest

from starlight_voice import adapters
from starlight_voice.config import Settings
from starlight_voice.voice_loop import _llm_service, _stt_service, _tts_service


@pytest.mark.parametrize("engine", ["deepgram", "faster-whisper", "unknown"])
def test_unimplemented_stt_is_rejected_before_import_or_network(engine):
    with pytest.raises(adapters.VoiceDepsUnavailable, match="not implemented"):
        _stt_service(Settings(stt_engine=engine))


@pytest.mark.parametrize("engine", ["cartesia", "kokoro", "piper", "unknown"])
def test_unimplemented_tts_is_rejected_before_import_or_network(engine):
    with pytest.raises(adapters.VoiceDepsUnavailable, match="not implemented"):
        _tts_service(Settings(tts_engine=engine))


def test_selected_flash_model_and_voice_reach_the_factory(monkeypatch):
    monkeypatch.setitem(sys.modules, "pipecat.services.elevenlabs.tts",
                        SimpleNamespace(ElevenLabsTTSService=lambda **kw: kw))
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    result = _tts_service(Settings(tts_model="eleven_flash_v2", tts_voice_id="chosen-voice"))
    assert result["model"] == "eleven_flash_v2"
    assert result["voice_id"] == "chosen-voice"


def test_dialogue_models_require_an_explicit_voice_before_optional_imports(monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    with pytest.raises(adapters.VoiceDepsUnavailable, match="explicit STARLIGHT_TTS_VOICE_ID"):
        _tts_service(Settings(tts_model="eleven_v4_turbo"))


@pytest.mark.parametrize("model", ["eleven_v4_turbo", "eleven_v3_conversational"])
def test_dialogue_model_uses_its_own_factory_and_never_flash(monkeypatch, model):
    from starlight_voice.adapters import eleven_dialogue_pipecat

    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    monkeypatch.setattr(eleven_dialogue_pipecat, "create_dialogue_tts", lambda **kw: kw)
    result = _tts_service(Settings(tts_model=model, tts_voice_id="chosen"))
    assert result == {"api_key": "test-key", "voice_id": "chosen", "model": model}


def test_unknown_tts_model_is_rejected_before_imports():
    with pytest.raises(adapters.VoiceDepsUnavailable, match="not implemented"):
        _tts_service(Settings(tts_model="unknown"))


@pytest.mark.parametrize("model", ["elevenlabs/eleven-v4-turbo", "elevenlabs/eleven-v3-conversational"])
def test_explicit_openrouter_speech_selection_reaches_only_its_factory(monkeypatch, model):
    from starlight_voice.adapters import openrouter_speech_pipecat

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-inference-key")
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.setattr(openrouter_speech_pipecat, "create_openrouter_speech_tts", lambda **kw: kw)
    result = _tts_service(Settings(tts_engine="openrouter-speech", tts_model=model, tts_voice_id="george"))
    assert result == {"api_key": "test-inference-key", "voice_id": "george", "model": model}


@pytest.mark.parametrize("key,voice", [("", "george"), ("*not found*", "george"), ("test", "")])
def test_openrouter_speech_needs_inference_key_and_selected_voice(monkeypatch, key, voice):
    monkeypatch.setenv("OPENROUTER_API_KEY", key)
    with pytest.raises(adapters.VoiceDepsUnavailable, match="inference OPENROUTER_API_KEY"):
        _tts_service(Settings(tts_engine="openrouter-speech", tts_model="elevenlabs/eleven-v4-turbo", tts_voice_id=voice))


def test_openrouter_speech_does_not_translate_or_fallback_from_a_direct_model_id():
    with pytest.raises(adapters.VoiceDepsUnavailable, match="explicit supported"):
        _tts_service(Settings(tts_engine="openrouter-speech", tts_model="eleven_v4_turbo", tts_voice_id="george"))


def test_grok_profile_uses_one_dedicated_key_for_all_three_stages(monkeypatch):
    from starlight_voice.adapters import openrouter_speech_pipecat, openrouter_transcription_pipecat

    monkeypatch.setenv("STARLIGHT_VOICE_PROFILE", "grok-openrouter")
    monkeypatch.setenv("STARLIGHT_VOICE_OPENROUTER_API_KEY", "dedicated-fixture")
    monkeypatch.setenv("OPENROUTER_API_KEY", "broken-shared-fixture")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.setattr(openrouter_transcription_pipecat, "create_openrouter_transcription_stt", lambda **kw: kw)
    monkeypatch.setattr(openrouter_speech_pipecat, "create_openrouter_speech_tts", lambda **kw: kw)

    class ModelService:
        Settings = SimpleNamespace

        def __init__(self, *, api_key, settings):
            self.api_key, self._settings = api_key, settings

    monkeypatch.setitem(sys.modules, "pipecat.services.openrouter.llm", SimpleNamespace(OpenRouterLLMService=ModelService))
    settings = Settings.from_env()
    assert _stt_service(settings) == {"api_key": "dedicated-fixture", "model": "x-ai/grok-stt-1.0"}
    assert _tts_service(settings) == {"api_key": "dedicated-fixture", "model": "x-ai/grok-voice-tts-1.0", "voice_id": "ara"}
    model = _llm_service(settings)
    assert model.api_key == "dedicated-fixture" and model._settings.model == "x-ai/grok-4.7"
    assert model._settings.extra == {"provider": {"order": ["xai"], "allow_fallbacks": False}, "reasoning": {"effort": "low"}}


def test_grok_cannot_retain_an_incompatible_cerebras_pin(monkeypatch):
    monkeypatch.setenv("STARLIGHT_VOICE_OPENROUTER_API_KEY", "fixture")
    with pytest.raises(adapters.VoiceDepsUnavailable, match="xai provider"):
        _llm_service(Settings(llm_model="x-ai/grok-4.7"))
