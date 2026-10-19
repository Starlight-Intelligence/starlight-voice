from __future__ import annotations

import os
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

ENV_FILES = (".env.local", ".env")
INFISICAL_DOMAIN = "https://eu.infisical.com"
INFISICAL_ORG = "a40109f1-3b54-477e-a7f9-805dd8abe602"
INFISICAL_PROJECT = "5578966b-0c74-4253-9a3c-bace21d97b24"


def infisical_enabled() -> bool:
    return os.environ.get("STARLIGHT_XVOICE_INFISICAL", "1").strip() != "0"


def infisical_secret(name: str) -> str:
    """One secret from the EU Infisical project, or "" when it cannot be read."""

    env_name = os.environ.get("STARLIGHT_XVOICE_INFISICAL_ENV", "dev").strip() or "dev"
    try:
        done = subprocess.run(
            [
                "infisical", "secrets", "get", name,
                "--plain", "--silent",
                "--domain", INFISICAL_DOMAIN,
                "--org", INFISICAL_ORG,
                "--projectId", INFISICAL_PROJECT,
                "--env", env_name,
                "--log-level", "error",
                "--telemetry=false",
            ],
            capture_output=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if done.returncode != 0:
        return ""
    return done.stdout.decode("utf-8", errors="replace").strip()


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

    Defaults are CLOUD-FIRST (decided 2026-06-15): on Frank's GTX 1650 (4GB, no Tensor
    cores) cloud STT/TTS is faster, frees the GPU for parallel work, and needs no heavy
    CUDA install. The runnable-today path uses only LIVE keys — Groq Whisper (STT),
    OpenRouter (LLM, FAST tier pinned to Cerebras THROUGH OpenRouter), ElevenLabs (TTS).
    Local engines (faster-whisper / Kokoro) remain selectable as an offline/private tier
    via env vars; install them with the `voice-local` extra.
    """

    # STT — cloud-first default
    stt_engine: str = "groq-openrouter"          # groq-openrouter | deepgram | faster-whisper(local) | groq
    stt_model: str = "whisper-large-v3-turbo"
    stt_compute_type: str = "int8"               # only used by the local faster-whisper tier
    stt_device: str = "cpu"                       # only used by local tier; cuda|cpu

    # LLM (via OpenRouter gateway per global doctrine)
    llm_model: str = "openai/gpt-5"
    llm_base_url: str = "https://openrouter.ai/api/v1"
    llm_fast_provider: str = "cerebras"          # OpenRouter provider pin for the FAST/voice tier (else TTFT SLA breaks)

    # TTS — cloud-first default (ElevenLabs key is live; Cartesia Sonic-3.5 when keyed)
    tts_engine: str = "elevenlabs"               # elevenlabs | cartesia | kokoro(local) | piper(local)
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
            tts_voice_id=_env("STARLIGHT_TTS_VOICE_ID", cls.tts_voice_id),
            tts_device=_env("STARLIGHT_TTS_DEVICE", cls.tts_device),
        )

    def to_dict(self) -> dict[str, object]:
        return asdict(self)
