import os

from starlight_voice.config import Settings, load_local_env


def test_load_local_env_reads_env_local_without_overwriting_process_env(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "process-wins")
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    (tmp_path / ".env.local").write_text(
        "OPENAI_API_KEY=file-loses\nELEVENLABS_API_KEY=eleven-test\n",
        encoding="utf-8",
    )

    loaded = load_local_env(tmp_path)

    assert loaded == [tmp_path / ".env.local"]
    assert os.environ["OPENAI_API_KEY"] == "process-wins"
    assert os.environ["ELEVENLABS_API_KEY"] == "eleven-test"


def test_settings_defaults_are_cloud_first() -> None:
    s = Settings()
    assert s.stt_engine == "groq-openrouter"   # cloud-first: live Groq+OpenRouter keys
    assert s.tts_engine == "elevenlabs"        # cloud-first: live ElevenLabs key
    assert s.llm_fast_provider == "cerebras"   # FAST tier must pin a sub-200ms-TTFT provider
    assert s.first_audio_p50_budget_ms == 800
    assert "stt_engine" in s.to_dict()


def test_settings_from_env_overrides(monkeypatch) -> None:
    monkeypatch.setenv("STARLIGHT_TTS_ENGINE", "kokoro")    # opt into the local tier
    monkeypatch.setenv("STARLIGHT_STT_ENGINE", "faster-whisper")
    s = Settings.from_env()
    assert s.tts_engine == "kokoro"
    assert s.stt_engine == "faster-whisper"
    assert s.llm_model == "openai/gpt-oss-120b"  # supported Cerebras route


def test_settings_from_env_blank_falls_back_to_default(monkeypatch) -> None:
    monkeypatch.setenv("STARLIGHT_TTS_ENGINE", "   ")
    assert Settings.from_env().tts_engine == "elevenlabs"


def test_tts_model_can_be_selected_without_changing_voice_identity(monkeypatch):
    monkeypatch.setenv("STARLIGHT_TTS_MODEL", "eleven_v4_turbo")
    monkeypatch.setenv("STARLIGHT_TTS_VOICE_ID", "stable-voice-id")
    settings = Settings.from_env()
    assert settings.tts_model == "eleven_v4_turbo"
    assert settings.tts_voice_id == "stable-voice-id"


def test_load_local_env_preserves_special_chars_and_strips_inline_comments(tmp_path, monkeypatch) -> None:
    for k in ("KEY_QUOTED", "KEY_PLAIN", "KEY_HASHVAL"):
        monkeypatch.delenv(k, raising=False)
    (tmp_path / ".env").write_text(
        # quoted value with '=' and '#' inside must survive verbatim
        'KEY_QUOTED="sk-or-v1=ab#cd"\n'
        # unquoted value with a trailing inline comment must be trimmed
        "KEY_PLAIN=hello world  # trailing note\n"
        # a literal '#' in an unquoted value without a leading space is kept
        "KEY_HASHVAL=abc#def\n",
        encoding="utf-8",
    )
    load_local_env(tmp_path)
    assert os.environ["KEY_QUOTED"] == "sk-or-v1=ab#cd"
    assert os.environ["KEY_PLAIN"] == "hello world"
    assert os.environ["KEY_HASHVAL"] == "abc#def"
