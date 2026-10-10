# Voice provider implementation update, 10 October 2026

Status: offline implementation in progress. End-to-end conversations, live latency,
voice preference and runtime resource use remain unverified.

## Provider boundaries

Native speech-to-speech and a chained STT/text-model/TTS pipeline serve different
jobs. Keep provider transports separate from domain tools, durable work intake,
permissions and memory. Long coding tasks return task IDs and outcomes asynchronously.

ElevenLabs v4 Turbo uses the Text to Dialogue WebSocket with registered voices,
`inputs` frames and a final `close_socket` flush. The existing Flash TTS connection
uses a different protocol. The new `ElevenDialogue` adapter implements the dialogue
protocol without importing Pipecat or opening devices. Its PCM/device integration
still needs live verification. [Official dialogue guide](https://elevenlabs.io/docs/eleven-api/guides/how-to/websockets/realtime-tdd)

The current component graph supports Groq transcription and ElevenLabs Flash or
Multilingual v2 speech. Selecting another engine now fails explicitly instead of
sending audio to the configured default. `STARLIGHT_TTS_MODEL` selects a supported
model independently of the voice ID. v4 Turbo is rejected by this graph until its
separate adapter is bound to it.

The component default now pairs `openai/gpt-oss-120b` with the existing Cerebras
provider pin, replacing an incompatible GPT-5/Cerebras selection. This is a
configured route, not a measured performance result. [Provider listing](https://openrouter.ai/provider/cerebras)

## Memory and readiness

Legacy loopback retrieval now uses an async request with a total deadline. The
client rejects external endpoints, redirects and unsuccessful responses, ignores
proxy environment variables, and bounds result counts. Cancellation propagates.
Retrieved text no longer becomes a system message. It remains explicitly untrusted
frame metadata until a typed tool-result adapter admits it to the model.

Gateway discovery means configured, not running. The status surface and CLI report
runtime verification separately from dependency presence. Neither file presence
nor headless graph construction proves microphone capture, playback, valid provider
credentials, tool execution or conversational latency.

## Verification

81 offline tests pass and two provider suites skip because the optional Pipecat
runtime is absent. Ruff passes. The local HTTP server suite was excluded to respect
a no-server-start constraint. New tests cover engine selection, async retrieval,
credential egress prevention, dialogue framing, final flush, malformed audio,
provider failure, timeout and cancellation cleanup. Fake transports establish
protocol and recovery behavior; no live synthesis claim follows from those checks.

## Remaining work

Bind the dialogue adapter to the component pipeline and a selected voice identity;
replace reference metadata with typed, bounded read-tool results; implement and
compare native conversation transports; verify authentication, interruption and
receipt recovery. Obtain independent revision review before release and measure
first playable audio, session closure, provider cost and resource use under admitted
runtime conditions. Keep this implementation distinct from a released daily driver.
