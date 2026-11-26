# Open It Up — Starlight Voice runbook

Status as of **2026-06-26** (autonomous build-out pass). This is the single page that tells
you how to launch your Jarvis, what is proven working, and the two things only you can fix.

## TL;DR — launch

```powershell
cd C:\Users\frank\starlight-voice
.\scripts\jarvis.ps1            # talk: full mic -> brain -> voice loop
.\scripts\jarvis.ps1 clap       # double-clap to summon, then talk
.\scripts\jarvis.ps1 smoke      # prove the whole cloud pipeline WITHOUT a mic
```

## What is proven working (verified live, not assumed)

| Stage | Engine | Result |
|---|---|---|
| Ears (STT) | Groq `whisper-large-v3-turbo` | ✅ ~640–770ms; round-trip recovered the words exactly |
| Brain (LLM) | OpenRouter `openai/gpt-oss-120b` **on Cerebras** | ✅ ~1.0s, real answers; falls back if Cerebras 429s |
| Brain (deliberate) | OpenRouter `anthropic/claude-sonnet-4.6` | ✅ 200 OK — used for hard reasoning |
| Mouth (TTS) | ElevenLabs `eleven_flash_v2_5`, voice **Daniel — Steady Broadcaster** | ✅ ~830ms — but see action #1 |
| Graph | Pipecat 1.4.0, 9 processors + Smart-Turn v3 | ✅ assembles clean |
| Activation | Claps (adaptive onset, no key) + push-to-talk | ✅ 5/5 detection tests pass |

Verify any time with `.\scripts\jarvis.ps1 smoke` (writes audio to `%TEMP%\starlight_smoke`).

## TWO THINGS ONLY YOU CAN FIX

1. **ElevenLabs is nearly out of credits.** The key "Lenovo Local Arcanea" has a quota of 50
   with ~12 credits left, so the premium Daniel voice will stop mid-sentence on real use.
   → Top up the ElevenLabs plan, **or** switch the voice engine (see Fleet below).
2. **`OPENAI_API_KEY` is invalid (401).** OpenAI TTS *and* the OpenAI Realtime fleet lane are
   dead until you paste a valid key into the environment. Everything else runs without it.

## The Fleet (switch engines per purpose)

All hot-swappable via env vars (`Settings.from_env`), no code change:

| Want | Set |
|---|---|
| Default (proven) | nothing — Groq STT + Cerebras gpt-oss-120b + ElevenLabs Daniel |
| A different voice | `STARLIGHT_TTS_VOICE_ID` (e.g. George `JBFqnCBsd6RMkjVDRZzb`, Julian `7p1Ofvcwsv7UBPoFNcpI`) |
| Smarter (slower) brain | `STARLIGHT_LLM_MODEL=anthropic/claude-sonnet-4.6` |
| Lowest-latency duplex | `voice --run --variant openai-realtime` (needs a valid OpenAI key) |

## Knowledgebase (make Jarvis project-aware) — next build

The voice graph already contains a `MemoryContextProcessor` that recalls from the **SIS Memory
Gateway** (`~/Starlight-Intelligence-System/src/gateway/daemon.ts`, hybrid retrieval). It
degrades to context-less if the daemon is down — which is the current state. To light it up,
the gateway daemon must run and write `gateway.json` where `memory.py` discovers it
(`~/Starlight-Intelligence-System/` or `~/.starlight/`). That is the Wave 3 task: start +
index the gateway, then turns become repo/project-aware automatically.
