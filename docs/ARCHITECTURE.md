# Starlight Voice Architecture

This is the implementation-facing architecture for the repo. `docs/SPEC.md` is the origin story; this file is the daily build contract.

## Design Posture

Starlight Voice is a local-first voice operator, not a SaaS voice bot.

The best current systems split concerns cleanly:

- Tauri owns operating-system integration: tray, hotkey, autostart, sidecar lifecycle.
- Python owns the agent runtime: voice pipeline, model adapters, browser automation, MCP tools.
- Browser work is a capability lane, not a hidden always-on browser at boot.
- Public docs must state what is real today and what is planned.

## Current Primary References

- Pipecat: open-source Python framework for realtime voice and multimodal agents, with service/transports/pipeline composition. <https://docs.pipecat.ai/overview/introduction>
- LiveKit Agents: production-ready voice AI agent framework and WebRTC deployment path. <https://docs.livekit.io/agents/>
- OpenAI Voice Agents / Realtime: useful browser/mobile WebRTC option and reference for session lifecycle. <https://developers.openai.com/api/docs/guides/voice-agents>
- browser-use: browser automation agent stack with Python API and native core runtime. <https://github.com/browser-use/browser-use>

## Architecture Decision

Default desktop path:

```text
Tauri tray + PTT hotkey
  -> Python sidecar JSON-lines IPC
  -> Pipecat pipeline
  -> STT / LLM / TTS providers
  -> speakers
```

Browser/mobile path:

```text
Phone or browser client
  -> WebRTC / Realtime transport
  -> sidecar or hosted relay
  -> same cognition/tool layer
```

Browser automation path:

```text
voice/text request
  -> CognitionRouter tier3-browser
  -> BrowserAutomationAdapter
  -> browser-use or hosted browser provider
```

## Operator Layers (decided 2026-11-05)

The voice engine is a part we choose and swap. The operator is what we build. Every engine sits under
the same kernel, so a cheaper or local engine can never gain powers Ara lacks, and Ara can never lose
the approval gate.

```text
Ear     clap gate (clap.py) | later: wake phrase, push-to-talk, phone capture
  |
Voice   engine, swappable:
        A. xAI Grok Voice realtime, voice ara          (primary, x_voice.py)
        B. Pipecat cascade: Silero VAD + Smart Turn v3
           + STT + LLM + TTS                            (fallback and offline lane, voice_loop.py)
        C. OpenAI Realtime / Gemini Live                (bake-off only, voice_engines.py)
  |
Kernel  operator_kernel.py: one tool registry, each tool tagged read | propose | consent,
        projected as realtime_schemas() or chat_schemas()
  |
Hands   estate_lookup, recall, estate_status, triage_*  -> read
        meta_harness -> proposal                         -> propose
        confirm_handoff (spoken go) + human approve      -> approval.py tier-B gate
                                                            -> one Queen card -> a harness seat
  |
Face    /live: phase, lane (Grok plan or xAI key), cap, plan pause, sessions
Memory  session_store (local) + tool_plane recall (starlight-memory)
```

What we adopt rather than build: Pipecat (transport, pipeline, interruption, Smart Turn v3 turn
detection), the xAI realtime socket, and later a Cloudflare Durable Object hub for phone and other
machines. What we build because nobody else has it: the kernel's single tool registry with authority
levels, the spoken-go plus human-ack gate, cross-harness dispatch through the Queen, estate memory,
and spend caps that fail closed. A second orchestrator (OpenJarvis, OpenClaw and similar) is out:
the Queen is the one chair.

Engine rules:

- An engine registers tools only through `operator_kernel`. Contract tests fail if a projection, Ara's
  session, or Ara's dispatcher drifts from the registry.
- A lower-trust engine (an unvetted local model, a phone relay) gets `authority={"read"}` until it has
  its own consent path tested.
- The engine never writes the human ack. That stays on the tray, the CLI, or the same-origin
  dashboard POST.

Next, in order: move Ara's tool dispatch (`_finish_tools`) behind the kernel so each tool has one
handler; give the Pipecat cascade the kernel's `chat_schemas(authority={"read"})` and the Smart Turn v3
analyzer, so it can answer when xAI is unavailable; then measure both engines with Harness Cup v2
(`contracts/harness-cup-v2.json`) for first-audio latency and tool accuracy on this machine.

## What Exists Now

- Tauri tray shell builds.
- Python sidecar package exists.
- JSON-lines IPC handles `health`, `utterance`, and `browser.task`.
- Machine doctor reports installed tools, optional package readiness, and missing agent lanes.
- Browser-use adapter supports dry-run routing and explicit live gating.
- Router classifies control, fast chat, deliberation, CLI-agent, and browser tasks.
- CI builds Rust and tests Python.
- Benchmarks smoke the low-latency routing path.

## What Must Exist Before Calling It Daily-Driver

- Global PTT hotkey wired to sidecar.
- Microphone capture and audio output.
- Pipecat voice pipeline with real STT/LLM/TTS adapters.
- MCP client with a constrained tool registry.
- Browser live execution sandbox policy.
- Installer with one hidden scheduled task.
- Legacy scheduled-task migration scripts.
- Latency gate for hot-path P50.

## Local Management Commands

```powershell
pwsh -File scripts/doctor.ps1
pwsh -File scripts/smoke-sidecar.ps1
pwsh -File scripts/test-local.ps1
```

## Performance Targets

See `benchmarks/budgets.toml`.

Non-negotiables:

- zero visible windows at logon
- one scheduled task owned by Starlight Voice
- router decision P50 under 10 ms
- dry-run browser routing P50 under 50 ms
- mic close to first audio P50 under 800 ms when provider path is live

## Interface Standard

The voice interface should feel calm, premium, and precise:

- no fake certainty
- short spoken responses by default
- immediate acknowledgement before long reasoning
- visible tray state: idle, listening, thinking, error
- no browser or dashboard unless explicitly requested
