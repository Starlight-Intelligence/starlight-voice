"""Headless end-to-end voice-pipeline smoke test — proves the cloud loop without a mic.

Exercises the real engines the live loop uses, round-tripping audio through them:

    text --[ElevenLabs TTS]--> mp3 --[Groq STT]--> text'   (ears+mouth proven)
    "What can you do?" --[OpenRouter LLM]--> reply --[ElevenLabs TTS]--> mp3   (brain proven)

So when you finally open the mic, every cloud hop behind it is already verified green.
Uses only httpx (no SDKs), reads keys from the environment, prints a JSON verdict with
per-stage latency. Picks the best Jarvis-grade ElevenLabs voice unless STARLIGHT_TTS_VOICE_ID
is set, and prints the chosen voice_id so it can be pinned in .env.local.

    $env:PYTHONPATH="sidecar/src"; .\.venv\Scripts\python.exe scripts\smoke_voice_e2e.py
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import httpx

OUT = Path(os.environ.get("TEMP", ".")) / "starlight_smoke"
OUT.mkdir(parents=True, exist_ok=True)

EL_KEY = os.environ.get("ELEVENLABS_API_KEY", "")
GROQ_KEY = os.environ.get("GROQ_API_KEY", "")
OR_KEY = os.environ.get("OPENROUTER_API_KEY", "")

# Preference order for a Jarvis-grade voice: authoritative, warm, articulate (British-leaning).
VOICE_PREFERENCE = ["Daniel", "George", "Julian", "Ryan", "Brian", "Adam", "Charlie"]
TTS_MODEL = "eleven_flash_v2_5"
STT_MODEL = os.environ.get("STARLIGHT_STT_MODEL", "whisper-large-v3-turbo")
# Voice-loop FAST tier: a Cerebras-hosted model for sub-200ms TTFT (snappy talk-back).
LLM_MODEL = os.environ.get("STARLIGHT_LLM_MODEL", "openai/gpt-oss-120b")
LLM_FAST_PROVIDER = os.environ.get("STARLIGHT_LLM_FAST_PROVIDER", "cerebras")


def _ms(t0: float) -> int:
    return round((time.monotonic() - t0) * 1000)


def pick_voice() -> tuple[str, str]:
    pinned = os.environ.get("STARLIGHT_TTS_VOICE_ID")
    if pinned:
        return pinned, "(pinned via STARLIGHT_TTS_VOICE_ID)"
    r = httpx.get("https://api.elevenlabs.io/v1/voices",
                  headers={"xi-api-key": EL_KEY}, timeout=20)
    r.raise_for_status()
    voices = {v["name"]: v["voice_id"] for v in r.json().get("voices", [])}
    for pref in VOICE_PREFERENCE:                       # substring match ("Daniel" -> "Daniel - Steady Broadcaster")
        for name, vid in voices.items():
            if name.split()[0].lower() == pref.lower():
                return vid, name
    name, vid = next(iter(voices.items()))
    return vid, name


def tts(text: str, voice_id: str, tag: str) -> Path:
    r = httpx.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
        headers={"xi-api-key": EL_KEY, "content-type": "application/json"},
        json={"text": text, "model_id": TTS_MODEL},
        timeout=60,
    )
    r.raise_for_status()
    p = OUT / f"{tag}.mp3"
    p.write_bytes(r.content)
    return p


def stt(audio: Path) -> str:
    with audio.open("rb") as fh:
        r = httpx.post(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {GROQ_KEY}"},
            files={"file": (audio.name, fh, "audio/mpeg")},
            data={"model": STT_MODEL},
            timeout=60,
        )
    r.raise_for_status()
    return r.json().get("text", "").strip()


def llm(prompt: str) -> str:
    r = httpx.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={"Authorization": f"Bearer {OR_KEY}", "content-type": "application/json"},
        json={
            "model": LLM_MODEL,
            "provider": {"order": [LLM_FAST_PROVIDER], "allow_fallbacks": False},
            "messages": [
                {"role": "system", "content": "You are Starlight, Frank's voice operator. "
                 "Reply in <=2 short sentences. Lead with the action."},
                {"role": "user", "content": prompt},
            ],
        },
        timeout=60,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()


def main() -> int:
    missing = [k for k, v in {"ELEVENLABS": EL_KEY, "GROQ": GROQ_KEY, "OPENROUTER": OR_KEY}.items() if not v]
    if missing:
        print(json.dumps({"ok": False, "error": f"missing keys: {missing}"}))
        return 2

    result: dict[str, object] = {"ok": True, "stages": {}, "artifacts": str(OUT)}
    try:
        voice_id, voice_name = pick_voice()
        result["voice"] = {"id": voice_id, "name": voice_name}

        t = time.monotonic()
        greeting = tts("Starlight online. All systems nominal.", voice_id, "01_greeting")
        result["stages"]["tts_greeting_ms"] = _ms(t)

        t = time.monotonic()
        heard = stt(greeting)
        result["stages"]["stt_ms"] = _ms(t)
        result["stt_heard"] = heard

        t = time.monotonic()
        reply = llm("What can you do for me?")
        result["stages"]["llm_ms"] = _ms(t)
        result["llm_reply"] = reply

        t = time.monotonic()
        spoken = tts(reply, voice_id, "02_reply")
        result["stages"]["tts_reply_ms"] = _ms(t)

        # round-trip sanity: did STT recover the greeting words?
        result["roundtrip_ok"] = "starlight" in heard.lower() or "nominal" in heard.lower()
        result["total_ms"] = sum(v for v in result["stages"].values() if isinstance(v, int))
    except httpx.HTTPStatusError as e:
        result["ok"] = False
        result["error"] = f"{e.request.url} -> {e.response.status_code}: {e.response.text[:200]}"
    except Exception as e:  # noqa: BLE001
        result["ok"] = False
        result["error"] = f"{type(e).__name__}: {e}"

    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
