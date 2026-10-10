from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from pathlib import Path

ENV_FILES = (".env.local", ".env")


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def load_local_env(root: Path | None = None) -> list[Path]:
    base = root or repo_root()
    loaded: list[Path] = []

    for name in ENV_FILES:
        path = base / name
        if not path.exists():
            continue

        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            # Quoted value: take content verbatim (preserves '#' and '=' inside). Unquoted:
            # drop a trailing ' # inline comment'. Avoids silently truncating real values.
            if value[:1] in ("'", '"'):
                quote = value[0]
                end = value.find(quote, 1)
                value = value[1:end] if end != -1 else value[1:]
            elif " #" in value:
                value = value.split(" #", 1)[0].strip()
            if key and key not in os.environ:
                os.environ[key] = value

        loaded.append(path)

    return loaded


def _env(key: str, default: str) -> str:
    value = os.environ.get(key, "").strip()
    return value or default


@dataclass(frozen=True)
class Settings:
    """Typed runtime configuration for the voice loop.

    Selection-only: this schema names WHICH engine/model/provider each stage uses.
    The engine implementations live in `adapters/`. The router (`cognition/router.py`)
    classifies tier and must NOT read this — provider-pin-by-tier is owned by the LLM
    adapter, TTS engine selection by the TTS adapter.

    Component defaults are CLOUD-FIRST (decided 2026-06-15): on Frank's GTX 1650
    (4GB, no Tensor cores), this avoids heavy CUDA installs. The configured path is
    Groq Whisper (STT), OpenRouter (LLM, FAST tier pinned to Cerebras through
    OpenRouter), ElevenLabs (TTS). Configuration does not prove working keys or
    measured speed; native browser speech is the primary conversation architecture.
    Local engines (faster-whisper / Kokoro) remain selectable as an offline/private tier
    via env vars; install them with the `voice-local` extra.
    """

    # STT — cloud-first default
    stt_engine: str = "groq-openrouter"          # groq-openrouter | deepgram | faster-whisper(local) | groq
    stt_model: str = "whisper-large-v3-turbo"
    stt_compute_type: str = "int8"               # only used by the local faster-whisper tier
    stt_device: str = "cpu"                       # only used by local tier; cuda|cpu

    # LLM (via OpenRouter gateway per global doctrine)
    llm_model: str = "openai/gpt-oss-120b"
    llm_base_url: str = "https://openrouter.ai/api/v1"
    llm_fast_provider: str = "cerebras"          # OpenRouter provider pin for the FAST/voice tier (else TTFT SLA breaks)

    # TTS — explicit component selection; live authentication/audition remain gates.
    tts_engine: str = "elevenlabs"               # elevenlabs | openrouter-speech | cartesia | kokoro | piper
    tts_model: str = "eleven_flash_v2_5"
    tts_voice_id: str = ""                       # engine-specific; adapter picks a sensible default when blank
    tts_device: str = "cpu"                       # only used by the local Kokoro/Piper tier

    # SLA (mirrors benchmarks/budgets.toml [hot_path])
    first_audio_p50_budget_ms: int = 800
    first_audio_p95_budget_ms: int = 1500

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            stt_engine=_env("STARLIGHT_STT_ENGINE", cls.stt_engine),
            stt_model=_env("STARLIGHT_STT_MODEL", cls.stt_model),
            stt_compute_type=_env("STARLIGHT_STT_COMPUTE_TYPE", cls.stt_compute_type),
            stt_device=_env("STARLIGHT_STT_DEVICE", cls.stt_device),
            llm_model=_env("STARLIGHT_LLM_MODEL", cls.llm_model),
            llm_base_url=_env("OPENROUTER_BASE_URL", cls.llm_base_url),
            llm_fast_provider=_env("STARLIGHT_LLM_FAST_PROVIDER", cls.llm_fast_provider),
            tts_engine=_env("STARLIGHT_TTS_ENGINE", cls.tts_engine),
            tts_model=_env("STARLIGHT_TTS_MODEL", cls.tts_model),
            tts_voice_id=_env("STARLIGHT_TTS_VOICE_ID", cls.tts_voice_id),
            tts_device=_env("STARLIGHT_TTS_DEVICE", cls.tts_device),
        )

    def to_dict(self) -> dict[str, object]:
        return asdict(self)
