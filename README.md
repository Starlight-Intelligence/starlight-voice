# Starlight Voice

Open-source reference implementation of a Jarvis-grade personal voice operator.

Starlight Voice is the extracted voice runtime for the Starlight ecosystem: a Tauri tray shell plus a planned Python sidecar for push-to-talk capture, low-latency STT/LLM/TTS, and MCP-driven tool access.

## Current State

This repo is **portable but not finished**.

What exists today:

- Rust/Tauri tray application scaffold
- Python sidecar scaffold with text-mode CLI
- JSON-lines IPC for `health`, `utterance`, and `browser.task`
- machine doctor for installed tools and readiness
- cognition router for fast, deliberation, browser, CLI-agent, and control paths
- browser automation adapter seam with safe dry-run mode
- public landing site (`site/`), mic-reactive voice experience (`dashboard/voice/`), and live operator console (`dashboard/`) sharing one design system
- benchmark smoke scripts and GitHub Actions CI
- committed `Cargo.lock` for reproducible builds
- first-principles spec at `docs/SPEC.md`
- implementation architecture at `docs/ARCHITECTURE.md`
- week 1-3 implementation plan at `docs/PLAN.md`
- handoff and pickup notes in `docs/`

What is still pending:

- push-to-talk hotkey behavior
- STT/LLM/TTS voice loop
- MCP server/client wiring
- live browser automation sandbox
- signed installer
- legacy SIS/Arcanea task migration scripts

## Adopt the clap ear

This is the path another machine can run without this estate.

1. Install Python 3.12 and the `x-voice` extra (`sounddevice`, `websockets`).
2. Sign in with the grok CLI, or set `XAI_API_KEY` to a value that starts with `xai-`. An OpenRouter key does not open this socket.
3. From the repo root, `python -m starlight_voice listen --check`. Then `python -m starlight_voice listen`.
4. Open `http://127.0.0.1:8765/live` for the face. The page does not open a microphone.
5. Two claps open Ara. One clap ticks. Two claps, or 45 seconds of quiet, close the socket. The day cap is 15 minutes.
6. A spoken work request emits one dispatch packet and does not start a coding agent.

The grok CLI session skips the xAI spending-limit check and does not turn on server search. An `xai-` key stays closed until `XAI_MANAGEMENT_KEY` and `XAI_TEAM_ID` can read a limit that still has room. Never commit either credential.

## Quick Start

Prerequisites:

- Windows 11
- Git
- Rust stable toolchain

Clone and build:

```powershell
git clone https://github.com/frankxai/starlight-voice.git C:\Users\frank\starlight\repos\starlight-voice
cd C:\Users\frank\starlight\repos\starlight-voice
cargo build --release -p starlight-voice-tauri
```

Run sidecar tests:

```powershell
python -m pip install pytest
$env:PYTHONPATH = "sidecar/src"
python -m pytest sidecar/tests
```

Inspect this machine:

```powershell
pwsh -File scripts/doctor.ps1
```

Run the tray scaffold:

```powershell
& .\target\release\starlight-voice-tauri.exe
```

Expected current behavior: a tray-only app starts without opening a terminal, browser, or main window.

Run the text-mode sidecar:

```powershell
$env:PYTHONPATH = "sidecar/src"
python -m starlight_voice health
python -m starlight_voice doctor
python -m starlight_voice say "open browser and search the docs"
python -m starlight_voice browser "open the Pipecat docs"
```

## Web surfaces

Starlight Voice ships three browser surfaces that share one set of design tokens
(`site/tokens.css` — the single source of truth):

- **Landing site** (`site/`) — a self-contained HTML5 + motion product page. No
  build step, no node toolchain; deploys to any static host (Vercel, GitHub Pages).
- **Voice experience** (`dashboard/voice/`) — a user-initiated microphone surface
  with a real local level signal, deterministic guided-turn states, keyboard PTT,
  interruption, reduced-motion behavior, and explicit provider-truth labels.
- **Operator console** (`dashboard/`) — a live status console that polls `/status`
  from the Python sidecar: voice-loop config, the architecture bake-off lanes,
  memory-gateway liveness, the dispatch ledger, and adapter availability.

Run both from one localhost origin (the server uses a fail-closed allowlist, so
only the landing, console, and JSON endpoints are exposed — never repo internals):

```powershell
$env:PYTHONPATH = "sidecar/src"
python dashboard/server.py
# landing  -> http://127.0.0.1:8765/
# voice    -> http://127.0.0.1:8765/voice/
# console  -> http://127.0.0.1:8765/dashboard/cockpit.html
```

To deploy just the landing site statically, publish the `site/` directory as-is.

## Configuration

Use the local secrets helper when provider wiring begins:

```powershell
pwsh -File scripts/set-secrets.ps1
```

It writes `.env.local`, which is gitignored. Do not paste provider keys into chat.
See `docs/SECRETS.md` for the local `.env.local` / LiteLLM / Infisical strategy.

## Install Guide

See `docs/INSTALL.md` for second-laptop setup and the current limitations.

Useful docs:

- `docs/USAGE.md` — how to run and test it
- `docs/CAPABILITIES.md` — what is real now versus planned
- `docs/FLOWS.md` — the target Jarvis-grade flows
- `docs/ARCHITECTURE.md` — engineering architecture
- `docs/PERPLEXITY_VOICE_IMPLEMENTATION_2026-07-18.md` — verified July 2026 reference evidence
- `docs/VOICE_EXPERIENCE_SCENE_BRIEF.md` — interaction, audio, motion, and QA contract
- `docs/SECRETS.md` — provider key setup and secret strategy

## Roadmap

The canonical implementation plan is `docs/PLAN.md`.

Milestones:

- Task 1-2: repo + Tauri tray scaffold shipped
- Task 3 partial: Python sidecar + IPC contract shipped
- Task 4 partial: CI + benchmark smoke shipped
- Current: machine doctor, capability docs, and usage scripts shipped
- Next: Rust sidecar process manager, tray menu, PTT, autostart IPC
- Task 10-16: first working voice loop
- Task 17-29: cognition router, MCP, installer, legacy-task migration, benchmark gate

## License

MIT. See `LICENSE`.
