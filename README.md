# Starlight Voice

Open-source reference implementation of a Jarvis-grade personal voice operator.

Starlight Voice is the extracted voice runtime for the Starlight ecosystem, with a Tauri tray scaffold, a Python broker and optional Pipecat conversation graph, and native browser voice with scoped MCP reads. Live voice quality, latency and RAM acceptance remain pending.

## Current state

The [10 October architecture assessment](docs/ARCHITECTURE-2026-10-10.md) records
current source boundaries, provider research, offline fixes and remaining runtime
acceptance. Provider quality, first-audio latency and session RAM remain unverified.

This repo is **portable but not finished**.

What exists today:

- Rust/Tauri tray application scaffold
- Python sidecar with text-mode CLI, native credential broker and optional component conversation graph
- JSON-lines IPC for `health`, `utterance`, and `browser.task`
- machine doctor for installed tools and readiness
- cognition router for fast, deliberation, browser, CLI-agent, and control paths
- browser automation adapter seam with safe dry-run mode
- public landing site (`site/`) and a live operator console (`dashboard/`) sharing one design system
- native Grok WebSocket and OpenAI WebRTC conversation paths with a local credential broker and scoped read tools; live audio remains unverified
- selectable ElevenLabs v4 Turbo/v3 Conversational dialogue binding for the Pipecat component graph, requiring an explicit account voice; device playback remains unverified
- an optional browser ElevenLabs component connection through the same dashboard broker; live authentication and audio remain unverified
- benchmark smoke scripts and GitHub Actions CI
- committed `Cargo.lock` for reproducible builds
- first-principles spec at `docs/SPEC.md`
- implementation architecture at `docs/ARCHITECTURE.md`
- week 1-3 implementation plan at `docs/PLAN.md`
- handoff and pickup notes in `docs/`

What is still pending:

- push-to-talk hotkey behavior
- live end-to-end STT/LLM/TTS acceptance on the configured accounts and audio device
- deployed signed memory projection and live recall acceptance; scoped MCP client wiring is implemented
- live browser automation sandbox
- signed installer
- legacy SIS/Arcanea task migration scripts

## Quick Start

Prerequisites:

- Windows 11
- Git
- Rust stable toolchain

Clone and build:

```powershell
git clone https://github.com/frankxai/starlight-voice.git C:\Users\frank\starlight-voice
cd C:\Users\frank\starlight-voice
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

Starlight Voice ships two browser surfaces that share one set of design tokens
(`site/tokens.css` — the single source of truth):

- **Landing site** (`site/`) — a self-contained HTML5 + motion product page. No
  build step, no node toolchain; deploys to any static host (Vercel, GitHub Pages).
- **Operator console** (`dashboard/`) — a live status console that polls `/status`
  from the Python sidecar: voice-loop config, the architecture bake-off lanes,
  memory-gateway liveness, the dispatch ledger, and adapter availability.

Run both from one localhost origin (the server uses a fail-closed allowlist, so
only the landing, console, and JSON endpoints are exposed — never repo internals):

```powershell
$env:PYTHONPATH = "sidecar/src"
python dashboard/server.py
# landing  -> http://127.0.0.1:8765/
# console  -> http://127.0.0.1:8765/dashboard/cockpit.html
```

For native browser voice, first install `python -m pip install -e "./sidecar[native]"`
and configure a server `XAI_API_KEY` or `OPENAI_API_KEY`. Start the server only when
you intend to run it; microphone capture begins with the console's Start button.
The native path has no Pipecat or local model dependency. Approved memory recall
requires the separately configured signed projection gateway and its per-result
`starlight.memory.projection-result.v1` metadata. Older unbound gateway responses
are refused; gateway adoption and signed snapshot verification remain pending.
Dispatch/approval remain blocked, and real voice quality, interruption, latency
and RAM need validation.

Grok interruption uses the browser's output-frame position and PCM sample counts
to estimate each queued content part's played duration. Graph lead time and gaps
between chunks are excluded. A missing output clock stops the session with a
recoverable reconnect message. Buffer and metadata limits bound local retention;
these checks do not establish audible latency or measured RAM.

The text CLI's `dispatch` command also builds previews only. Its legacy direct
CLI launch path has been removed. `dispatch --live` reports `unavailable` and
exits with code 2; always-ask requests remain `hard-blocked`. No harness starts
and no task is queued. Suggested fleet targets and keyword tiers are unverified
preview hints. Connecting the accepted managed controller remains open.

The native client can be imported without mounting controls. An embedding page can
set a same-origin `brokerPrefix` and `workletPath`, then explicitly mount and dispose
its controls. Disposal stops owned media and removes page listeners, including when
capability discovery is still pending. See the embedding section in
`docs/ARCHITECTURE-2026-10-10.md`. Command Center's existing Voice page and fixed
same-origin broker relay are implemented in
[draft PR 70](https://github.com/frankxai/starlight-command-center/pull/70), with a
synthetic three-width mission/Stop journey. Deployment and real audio acceptance
remain open.

To deploy just the landing site statically, publish the `site/` directory as-is.

## Configuration

For the browser component route, install `python -m pip install -e "./sidecar[voice-browser]"`
only under machine admission. Configure `GROQ_API_KEY`, an inference
`OPENROUTER_API_KEY`, and `STARLIGHT_TTS_VOICE_ID`. Direct ElevenLabs synthesis
also needs `ELEVENLABS_API_KEY`; the explicit OpenRouter speech option below uses
the OpenRouter key. The console lists only the configured voice, without assigning
it a licensed name or a listening preference. Configuration presence does not
verify authentication.

This optional route connects browser audio to the existing Pipecat graph over
SmallWebRTC. It requires Pipecat 1.12 or newer for detector cleanup and installs
WebRTC codecs and ONNX dependencies. It uses local Silero detection, while STT,
text inference and synthesis use cloud providers. Native Grok/OpenAI remain the
lighter alternative. No microphone, model, pipeline or async host loop starts
during discovery. Start reserves one five-minute, 20-read scope; its SDP offer
creates the runtime. Stop revokes reads and releases media before freeing that
reservation. Failed cleanup holds the slot and exposes Retry cleanup. The existing
server owns the demand-created async loop until server shutdown; no extra service
is registered. See the architecture document for transport and verification limits.

The standalone local component conversation needs speech boundaries for Groq's
segmented transcription. Its existing `voice`/`providers` runtime supplies the
Silero analyzer; no additional extra is required. An authorized local run binds
Silero voice detection to the user aggregator; headless graph assembly and native
browser voice do not load it. The existing shell does not supply push-to-talk
events to this graph. Voice detection quality, interruption and its RAM footprint
still need device verification. No model or audio session starts during discovery.

The component graph exposes five read tools through the existing native broker:
portfolio status, recent receipts, node health, approved memory recall and project
context. Tool results enter the conversation as untrusted reference data. A local
read scope lasts five minutes and allows 20 calls; interruption cancels pending
reads, and Stop releases the scope. The graph does not draft, dispatch or approve
work. Memory recall still requires a gateway with accepted result-binding metadata.

The broker owns each asynchronous read within its accepted scope. Stop, expiry and
scope replacement cancel those reads; authorization is checked before IO and again
before publishing a result. Late results from transports that suppress cancellation
are discarded. Existing synchronous ledger reads still have no wall-clock guarantee.

The component comparison graph has an explicit OpenRouter REST speech option:
set `STARLIGHT_TTS_ENGINE=openrouter-speech`, choose
`STARLIGHT_TTS_MODEL=elevenlabs/eleven-v4-turbo` or
`elevenlabs/eleven-v3-conversational`, and set `STARLIGHT_TTS_VOICE_ID` to a
premade voice name or supported ID. It requires an inference
`OPENROUTER_API_KEY`; a management key must stay outside the audio graph.
The adapter streams bounded 24 kHz mono PCM16 and closes its HTTP response on
interruption. The direct ElevenLabs WebSocket path and native browser defaults
remain selectable. Live authentication, audible quality, latency and billing
acceptance are pending. See the REST speech comparison in
`docs/ARCHITECTURE-2026-10-10.md` before enabling it.

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
