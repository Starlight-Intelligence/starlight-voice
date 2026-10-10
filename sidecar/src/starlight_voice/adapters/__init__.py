"""Voice I/O adapter seams (STT / LLM / TTS).

These Protocols are the stable contract the in-process Pipecat graph (`voice_loop.py`)
binds to. Concrete engines are added per the v2.1 implementation sequence AFTER the
on-device first-audio bench proves the SLO on this GTX 1650 — we do not build the full
graph against an assumed latency budget.

Gating mirrors `browser.py`: each engine declares the import it needs; `availability()`
reports what is installed via `importlib.util.find_spec` WITHOUT importing heavy CUDA/ONNX
modules, so `doctor`, `health`, and the bench skeleton stay import-safe on a bare machine.
"""

from __future__ import annotations

import importlib.util
from typing import Protocol, runtime_checkable


@runtime_checkable
class SttEngine(Protocol):
    def transcribe(self, pcm: bytes, sample_rate: int) -> str:
        """Transcribe a PCM clip to text (push-to-talk: record -> release -> transcribe)."""
        ...


@runtime_checkable
class LlmEngine(Protocol):
    async def stream(self, messages: list[dict[str, str]]):  # -> AsyncIterator[str]
        """Stream assistant token deltas. FAST tier pins a sub-200ms-TTFT provider."""
        ...


@runtime_checkable
class TtsEngine(Protocol):
    async def stream(self, text_chunks):  # -> AsyncIterator[bytes]
        """Stream PCM/audio bytes sentence-by-sentence from the LLM token stream."""
        ...


class VoiceDepsUnavailable(RuntimeError):
    """Raised when a selected engine's optional deps are not installed.

    Carries the exact extras to install, matching the browser.py 'live not wired' posture.
    """


# engine name -> the import that proves its deps are present.
# Kept as find_spec checks (no import) so this module is safe on a machine without CUDA/ONNX.
_ENGINE_IMPORTS: dict[str, str] = {
    "faster-whisper": "faster_whisper",
    "groq-openrouter": "openai",      # OpenRouter /audio/transcriptions via OpenAI-compatible client
    "deepgram": "deepgram",
    "openrouter": "openai",           # LLM via OpenRouter
    "openrouter-speech": "httpx",     # explicit REST PCM speech alternative
    "kokoro": "kokoro_onnx",
    "cartesia": "cartesia",
    "elevenlabs": "websockets",       # ElevenLabs stream-input over raw websockets
    "piper": "piper",
    "pipecat": "pipecat",
}

INSTALL_HINT = "uv pip install -e .[voice,providers]"


def _present(import_name: str) -> bool:
    return importlib.util.find_spec(import_name) is not None


def availability() -> dict[str, bool]:
    """Report which engines are installable-and-present, by import probe (no heavy import)."""
    return {engine: _present(import_name) for engine, import_name in _ENGINE_IMPORTS.items()}


def require(engine: str) -> None:
    """Gate: raise VoiceDepsUnavailable with an actionable hint if the engine isn't installed."""
    import_name = _ENGINE_IMPORTS.get(engine)
    if import_name is None:
        raise VoiceDepsUnavailable(f"Unknown engine '{engine}'. Known: {sorted(_ENGINE_IMPORTS)}")
    if not _present(import_name):
        raise VoiceDepsUnavailable(
            f"Engine '{engine}' needs '{import_name}', which is not installed. Install: {INSTALL_HINT}"
        )
