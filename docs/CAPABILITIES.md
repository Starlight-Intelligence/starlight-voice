# Capabilities

This file separates real capabilities from planned ones.

## Real Now

| Capability | Status | How to test |
|---|---|---|
| Tauri tray shell builds | working | `cargo build --release -p starlight-voice-tauri` |
| Sidecar health | working | `python -m starlight_voice health` |
| Machine doctor | working | `python -m starlight_voice doctor` |
| Text-mode routing | working | `python -m starlight_voice say "hello"` |
| Deliberation classification | working | `python -m starlight_voice say "think hard about architecture"` |
| Browser-task routing | dry-run | `python -m starlight_voice browser "open docs"` |
| Python tests | working | `python -m pytest sidecar/tests` |
| Benchmark smoke | working | `python benchmarks/run.py --probe router --n 25` |
| Agent CLI inventory | working through `arco` | `arco doctor` |

## Installed / Available On This Laptop

| Tool | State | Role |
|---|---|---|
| Python 3.13 | installed | sidecar runtime for now |
| uv | installed | future Python environment manager |
| Rust/Cargo | installed | Tauri shell build |
| Node/npm | installed | global CLI installation |
| PowerShell 7 | installed | Windows scripts |
| Claude Code | installed | deep coding and architecture lane |
| Codex | installed | coding-agent lane |
| OpenCode | installed | alternate coding-agent lane |
| `@arcanea/orchestrator` / `arco` | installed | agent router |
| Gemini CLI | missing | optional additional agent lane |
| browser-use | installed | browser automation runtime, live policy still pending |
| Anthropic SDK | installed | direct Claude provider lane |
| OpenAI SDK | installed | OpenAI/Cerebras-compatible provider lane |
| sounddevice | installed | local audio IO |
| Pipecat | installed (v1.3+) | realtime voice pipeline |

## Planned Next

| Capability | Why it matters |
|---|---|
| Rust sidecar process manager | tray owns lifecycle instead of shelling out ad hoc |
| Tray menu | pause, health, quit, settings |
| Global PTT | `Ctrl+Shift+Space` in Tauri tray (toggle voice loop) |
| Audio capture/playback | `voice --run` + Pipecat LocalAudioTransport |
| Pipecat pipeline | Groq STT → OpenRouter/Cerebras → ElevenLabs TTS |
| Browser-use live sandbox | real web task execution with receipts; package exists, policy and wiring pending |
| MCP client | safe access to SIS/Arcanea/Codex tools |
| Installer | one hidden scheduled task, no boot chaos |

## Non-Negotiable Constraints

- no visible terminal at logon
- no browser at logon
- no secret in Git
- no background process without health, logs, and uninstall
- browser automation requires explicit user intent
- destructive browser or CLI actions require approval policy before execution
