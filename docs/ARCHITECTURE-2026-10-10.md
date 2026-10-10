# Voice provider implementation update, 10 October 2026

Status: offline implementation in progress. End-to-end conversations, live latency,
voice preference and runtime resource use remain unverified.

## Provider boundaries

Native speech-to-speech and a chained STT/text-model/TTS pipeline serve different
jobs. Keep provider transports separate from domain tools, durable work intake,
permissions and memory. Long coding tasks return task IDs and outcomes asynchronously.

ElevenLabs v4 Turbo uses the Text to Dialogue WebSocket with registered voices,
`inputs` frames and a final `close_socket` flush. The existing Flash TTS connection
uses a different protocol. `ElevenDialogue` implements the dialogue protocol without
importing Pipecat or opening devices. Its lazy Pipecat binding emits 48 kHz mono
PCM16 frames with context IDs, errors and interruption cleanup. Device playback
still needs live verification. [Official dialogue guide](https://elevenlabs.io/docs/eleven-api/guides/how-to/websockets/realtime-tdd)

The component graph supports Groq transcription and ElevenLabs Flash, Multilingual
v2, v4 Turbo or v3 Conversational speech. `STARLIGHT_TTS_MODEL` selects the protocol
independently of the voice ID. Dialogue models require an explicit
`STARLIGHT_TTS_VOICE_ID` and `ELEVENLABS_API_KEY`. Each aggregated sentence uses a
fresh dialogue connection and flush; prosody is not carried across sentences.
Unknown models and engines fail before provider calls. The binding uses Pipecat
1.3 APIs; the voice extra now requires at least that version.
[Pipecat 1.3 TTS source](https://github.com/pipecat-ai/pipecat/blob/v1.3.0/src/pipecat/services/tts_service.py)

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

130 offline Python tests and 13 Node tests pass; three optional Pipecat tests skip.
Ruff passes. The local HTTP server suite remains excluded under the no-server-start
constraint. Hosted CI can exercise that suite on its own runners. Fake transports
establish protocol and cleanup behavior, not live audio quality or latency.

## Native browser conversation

The existing loopback console now has an explicit Start, Mute and Stop flow. Grok
uses `grok-voice-think-fast-2.0`, Ara or Orion, a short-lived WebSocket subprotocol
credential, PCM16 at 48 kHz and `reasoning.effort: none`. OpenAI is a separate
WebRTC choice using `gpt-realtime-2.1` and Marin or Cedar. These are account-access
and listening candidates, not measured winners. Neither transport uses browser
speech synthesis. [Grok speech guide](https://docs.x.ai/developers/model-capabilities/audio/speech-to-speech),
[OpenAI WebRTC guide](https://developers.openai.com/api/docs/guides/voice-webrtc?voice-api=realtime)

The broker issues credentials only on same-origin JSON POSTs with an exact local
Host and Origin. It rejects transfer encoding, duplicate or excessive body lengths,
arbitrary provider endpoints, models, instructions and tool definitions. API keys
stay server-side. Responses are not cached, the console refuses framing and its
content security policy limits executable content and network destinations.

One local session is admitted at a time with a 60-second start cooldown and 20
typed tool calls. Local tool authority expires after five minutes. Stop releases
that authority and the browser closes media tracks, playback sources, worklet,
socket or peer. Hidden tabs and Escape also stop capture. The client drops audio
after interruption and bounds both upload and playback backlogs. Parallel function
results arrive before one continuation response; cancelled turns do not trigger
that continuation.

These are local authority limits, not provider spending enforcement. Ephemeral
credentials can establish multiple provider sessions until expiry, provider session
settings can be client-controlled, and closing local authority cannot revoke a
provider connection elsewhere. The five-minute browser timer is best effort.
An abandoned credential request can hold the local slot until expiry. Hard provider
caps/revocation require a provider-side or trusted transport-control integration.
[OpenAI credential limitations](https://developers.openai.com/api/reference/resources/realtime/subresources/client_secrets/methods/create),
[Grok ephemeral credentials](https://docs.x.ai/developers/model-capabilities/audio/ephemeral-tokens)

Seven narrow function names follow the existing Voice Edge proposal: portfolio
counts, local run statuses, shared-plane response health, approved memory/project
summaries, dispatch and approval. Shared-plane reads use its existing authenticated
HTTP MCP endpoint; no raw task text, paths or machine bearer reaches the model.
Dispatch and approval currently return `blocked`, `queued: false`, `approved: false`.
They must connect to the existing durable managed intake before claiming otherwise.
This browser broker does not run coding CLIs or install another task queue.

Native recall uses the existing starlight-memory signed cloud-projection gateway,
whose exporter and gateway verify the manifest and approved atom bytes. It requires
an explicit numeric-loopback `/mcp` endpoint and separate server bearer through
`STARLIGHT_VOICE_PROJECTION_MCP_URL` and `STARLIGHT_VOICE_PROJECTION_MCP_TOKEN`.
The client checks gateway identity and projection policy/watermark, handles JSON or
SSE, applies a two-second total read deadline, bounds summaries, rejects redirects
and proxy settings, and deletes only its own MCP session. It never falls back to
the canonical private memory backend. The gateway performs signature verification;
the Voice client trusts this explicitly configured local service. Approved summaries
are delivered as function output with no instruction authority. Raw audio, transcripts
and tool arguments are not persisted by this path; provider retention remains governed
by the chosen account and provider terms.

For a later authorized start, install the lightweight `./sidecar[native]` extra,
configure a server `XAI_API_KEY` or `OPENAI_API_KEY`, and run the existing dashboard
command. The server loads its local environment only on explicit startup. The
native path requires neither Pipecat nor local model/audio-device wheels. No RAM
claim follows until a real browser/session is measured.

## Remaining work

Select and listen to an account voice through the ElevenLabs component pipeline;
connect durable dispatch/approval, verified memory deployment and turn
receipts; verify real provider authentication and audio interruption. Browser
appearance, touch and keyboard behavior need inspection under an authorized runtime
or preview. Compare native conversation and GPT Live delegation on matched tasks.
Obtain independent revision review before release and measure
first playable audio, session closure, provider cost and resource use under admitted
runtime conditions. Keep this implementation distinct from a released daily driver.
