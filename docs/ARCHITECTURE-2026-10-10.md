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

The current ElevenLabs guide explicitly includes v4 Turbo, while the WebSocket API
reference still describes v3-only model IDs. Keep v4 as a selectable candidate,
retain the Flash default, and verify account/model acceptance before playback.
[WebSocket reference](https://elevenlabs.io/docs/api-reference/text-to-dialogue/ttd-websocket)

The component default now pairs `openai/gpt-oss-120b` with the existing Cerebras
provider pin, replacing an incompatible GPT-5/Cerebras selection. This is a
configured route, not a measured performance result. [Provider listing](https://openrouter.ai/provider/cerebras)

## Component read tools

The component graph registers `portfolio_status`, `recent_run_receipts`,
`node_health`, `sis_recall_projection` and `project_context` with Pipecat's text
LLM service and advertises their schemas through `LLMContext`. It reuses the
native broker's strict argument validation, call-ID replay checks, 20-call budget,
fixed read endpoints and projection result binding. Local read authority expires
after five minutes and mints no provider credential. It has no HTTP start route.
Mission drafting, dispatch, approval and private-vault access are excluded.
Recent receipts read at most the last 64 KiB of each local ledger and disclose
five summaries, avoiding full dispatch-history allocation in the voice path.
This byte bound does not establish a wall-clock disk-read or conversation deadline.

Results are tool messages with `instruction_authority: false`; the system prompt
treats them as untrusted reference data. Failed, expired or denied reads return a
redacted unavailable result. The gateway must supply accepted result-binding
metadata for memory recall. Gateway deployment, signature verification and live
provider use remain open acceptance items.

The serious alternative was the graph's prior automatic recall on transcription.
That path stored reference text in frame metadata that the LLM context never
consumed, while still making a private gateway request. Explicit tool calls remove
those hidden reads and let the model request an approved summary when needed.
This establishes usable context wiring, not measured answer quality or latency.

Start creates local read authority without opening a device or provider socket.
Interruption invalidates the result generation, then Pipecat cancels its registered
function tasks and marks their conversation entries cancelled. Cancelling those
SDK tasks in the lifecycle processor first would leave unfinished tool entries.
The SDK test covers a successful new read after interruption. End,
Cancel and cleanup release the scope before accepting another result. The same
scope remains available after an interruption for the next turn; it is not renewed.
Focused synthetic tests cover validation, replay, expiry, budget and late-result
suppression. Hosted Pipecat 1.3.0 and 1.12.0 tests exercise the real SDK executor,
context aggregator and lifecycle without audio devices or provider requests.
Their exact revision and results belong in the task evidence; live audio and
provider continuation still require account and device acceptance.

## OpenRouter REST speech comparison

The component graph can explicitly select `STARLIGHT_TTS_ENGINE=openrouter-speech`
with `STARLIGHT_TTS_MODEL=elevenlabs/eleven-v4-turbo` or
`elevenlabs/eleven-v3-conversational`, an explicit `STARLIGHT_TTS_VOICE_ID` and an
inference `OPENROUTER_API_KEY`. The [7 October OpenRouter announcement](https://openrouter.ai/blog/announcements/elevenlabs-on-openrouter/)
documents premade named voices and a speech route without a separate ElevenLabs
plan. This can reduce credential setup for a component comparison. It does not
provide native speech-to-speech or the direct dialogue socket's text-input protocol.
No automatic protocol translation, provider fallback or default voice change occurs.

`OpenRouterSpeech` sends one aggregated sentence to the fixed official HTTPS
endpoint with an explicit model/voice and `response_format=pcm`. The
[TTS guide](https://openrouter.ai/docs/guides/overview/multimodal/tts) documents
24 kHz mono PCM16 for this ElevenLabs route. The adapter rejects redirects,
non-200 responses, non-PCM MIME types and compression before audio delivery,
ignores environment proxies, limits input to 4,096 characters/chunks and output
to 2 MiB, and bounds the whole turn to 30 seconds by default. It emits complete
PCM16 samples in chunks of at most 8 KiB without waiting to fill that size.
Raw HTTP chunk boundaries can split a sample; an incomplete final sample fails.
There are no retries or silent switches. Caller cancellation propagates and the
response/client close when the consumer closes the generator. The lazy Pipecat
binding labels frames as 24 kHz mono, retains context IDs and emits fatal redacted
errors for failed or silent synthesis. The base graph handles sentence ordering.
Both speech bindings cancel only the synthesis task belonging to the interrupted
context. A late interruption of an older sentence must not cancel the next one.

The named serious alternatives remain the direct ElevenLabs 48 kHz dialogue
WebSocket and native Grok/OpenAI conversation transports. REST requires a complete
sentence before submission and adds a gateway hop; neither that observation nor
vendor latency figures establishes which produces earlier audible speech.
Interrupted-stream cleanup in synthetic transport tests does not prove provider
cancellation or stopped charges. Each sentence starts a fresh request without
cross-sentence prosody context. Account/model acceptance, device playback,
stream-arrival/audible latency, voice preference, billing and RAM measurements
remain open. No account generation or device has been started. SDK checks cover
both Pipecat 1.3 and 1.12 using fake synthesis, not provider audio.

The discovered ordinary OpenRouter key currently fails GET authentication because
its expiry exceeds the account's maximum lifetime. The separate management key
authenticates for metadata and must stay outside inference. Approval for a short
dedicated capped test key is pending. Credentials are not copied into this repo;
Infisical's `*not found*` sentinel is rejected as a missing key.

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

The current query/watermark regressions, broker and turn-observation suites pass
86 tests locally; Ruff passes. Full per-OS, native-client, SDK and Rust checks run
in hosted CI. The local HTTP server suite remains excluded under the no-server-start
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

Stop invalidates the session epoch immediately, then attempts each owned resource
independently. One thrown close or disconnected view cannot skip later resources
or local authority release. Parallel Stop calls share the same cleanup operation.
New capture stays blocked while cleanup is pending or a failed action remains.
The page shows "Stopping…", then either completion or an explicit "Retry cleanup"
action with a reload fallback. Successful actions are discarded; retries retain
only failed actions and reuse an already-pending close promise. Late close success
clears the block, and a rejected playback promise from an old epoch cannot stop
a replacement session.

The foreground timer allows one second for asynchronous resource close and two
seconds for final metadata flush before the existing three-second authority
release request. Expired waits do not claim a resource was released. Browser
timer scheduling and actual device cleanup remain unverified; this is not a hard
wall-clock or provider-billing bound. A new page or separate client instance does
not inherit another instance's cleanup state. See the [Web Audio close contract](https://webaudio.github.io/web-audio-api/#dom-audiocontext-close)
and [media playback promise contract](https://html.spec.whatwg.org/multipage/media.html#dom-media-play).

| Before | After | Why |
| --- | --- | --- |
| Completion reported while audio close was pending | Visible stopping state; Start stays disabled | Avoid overlapping capture and uncertain cleanup |
| A failed close could skip remaining cleanup | Independent close attempts and retained retry actions | Release unaffected resources and preserve recovery |
| Old playback failure could stop the replacement session | Playback rejection checks its original epoch | Keep late errors with their owning session |

These are local authority limits, not provider spending enforcement. Ephemeral
credentials can establish multiple provider sessions until expiry, provider session
settings can be client-controlled, and closing local authority cannot revoke a
provider connection elsewhere. The five-minute browser timer is best effort.
An abandoned credential request can hold the local slot until expiry. Hard provider
caps/revocation require a provider-side or trusted transport-control integration.
[OpenAI credential limitations](https://developers.openai.com/api/reference/resources/realtime/subresources/client_secrets/methods/create),
[Grok ephemeral credentials](https://docs.x.ai/developers/model-capabilities/audio/ephemeral-tokens)

After a slow tool finishes, the client rechecks interruption, response generation
and session ownership before requesting a continuation. A barge-in, replacement
response or stopped/replaced session cannot restart the old reply. Four regression
cases reproduced the earlier race; the unchanged parallel-tool turn still resumes
once. These are offline event-order checks, not a live interruption measurement.

Eight narrow function names follow the existing Voice Edge proposal: portfolio
counts, local run statuses, shared-plane response health, approved memory/project
summaries, unsaved mission drafts, dispatch and approval. Shared-plane reads use its existing authenticated
HTTP MCP endpoint; no raw task text, paths or machine bearer reaches the model.
`node_health` now initializes the advertised `starlight-shared-tool-plane`, then
sends a protocol `ping`. It calls no telemetry or portfolio tool and reports
`scope: mcp-ping-only`, with hardware and audio health explicitly unverified.
One read through the actual broker completed in 362 ms against the already-running
service, including its own successful session DELETE. This is one reachability
observation; audible latency and fleet health remain unmeasured. Portfolio counts
still depend on the separately bounded active-session tool and may be unavailable.
Dispatch and approval currently return `blocked`, `queued: false`, `approved: false`.
They must connect to the existing durable managed intake before claiming otherwise.
This browser broker does not run coding CLIs or install another task queue.

Each tool call id is bound to its validated tool name and arguments using a
canonical JSON fingerprint held in session memory. Reusing an id for a different
tool or query returns a redacted 409 before reading or spending another tool call,
including while the original read is pending or after cancellation. Identical
retries retain the original result or pending/cancelled status; reordered argument
keys are equivalent. A cancelled read needs a new call id for a fresh attempt.
The existing 20-call limit bounds these fingerprints. Neither fingerprints nor
arguments are added to persisted turn observations. This prevents a reused id
from presenting an old project summary or mission draft as the answer to a new
request. Session authorization and expiry are still checked before replay.

### Read lifetime and late-result refusal

Each asynchronous broker read belongs to its accepted Session object. The broker
copies validated arguments before starting the task, rechecks authority before IO,
and checks the same owner again before caching or returning a result. Stop and
scope release revoke authority and schedule cancellation on each owned task's loop,
including when the request arrives on another HTTP thread. Replacing an expired
scope cancels its reads without affecting the new scope or other application tasks.
An expired component Stop uses the same revocation path; stale cleanup cannot
release a replacement session. Runtime-owned scopes retain their existing media
cleanup reservation rules.

The read's remaining scope lifetime also bounds its asynchronous wait. If a
transport suppresses cancellation and returns a late result, the broker refuses
that result. An SDK caller's cancellation still propagates so Pipecat can mark
the tool entry cancelled and continue on the next turn. These are cooperative
coroutine boundaries. Synchronous ledger and bearer-file reads can still block
the loop; this change establishes no hard disk, audible latency or RAM guarantee.
Existing MCP exchange and teardown bounds remain in force.

The previous browser-only suppression checked late tool results at the client,
while a broker read could continue and return after its authority ended. Broker
ownership now closes that server-side gap as well. Six regression cases reproduced
the old behavior before repair. Headless tests cover cross-thread Stop, expiry,
scope replacement, queued reads, caller argument mutation and eager task factories.
Actual Pipecat SDK continuation remains a hosted gate; live account and device
acceptance remain separate. No provider connection or billing revocation is claimed.

`prepare_mission` accepts only a bounded objective and acceptance text (15–900
characters each, measured as browser UTF-16 units). The broker and shared host
validator reject C0/C1 controls except tab/line breaks, explicit bidi embeddings,
overrides/isolates and unpaired surrogates. Ordinary Arabic, accents, emoji and
joiners remain valid; text is rejected rather than silently stripped. Invalid
draft fields fail before consuming the local tool budget. Command Center reuses
the same validator with its existing 1600-unit edited-field limit before saving.
It returns an opaque draft id with `queued: false` and
`approved: false`; draft contents stay in session memory. The native host may
display an editable draft, and Command Center lets the person choose product,
role, source and budgets before explicitly saving through its existing mission
proposal endpoint. No spoken yes, model output or provider callback saves,
approves or starts work. Client callbacks reject malformed/authority-bearing
drafts and results arriving after Stop, disposal or a replacement session.

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

### MCP client setup cost

The loopback MCP reader reuses one lazily loaded, verified certificate context
with HTTPX's existing certifi trust bundle. Each exchange still creates and closes
its own HTTP client and MCP session. Tokens, headers, connection pools and result
authority are separate; proxy/redirect refusal and scoped teardown remain in force.
Certificate verification and hostname checks remain enabled. Imports and discovery
load no certificate bundle. A process restart reloads changed trust bundles.
See [HTTPX's SSL context configuration](https://www.python-httpx.org/advanced/ssl/)
and [Python's context-sharing contract](https://docs.python.org/3.12/library/ssl.html#ssl.SSLContext).

On this machine, six metadata-only reads through the already-running shared MCP
plane had a median of 253 ms before this change. Tracing attributed 167–238 ms
per call to HTTP client construction, while requests took 3–22 ms to reach headers.
Six candidate reads had a median of 27 ms, with subsequent reads taking 20–28 ms.
The first candidate read still took 555 ms. These small samples include broker
authorization, MCP initialization/ping and cleanup; they read no private content
and started no app, model or audio device. They establish a measured reduction in
repeated metadata setup on this machine, without establishing recall, speech,
full-runtime RAM or competitive latency. Exact source hashes and raw samples are
preserved in the task evidence.

The alternative is retaining HTTP clients or initialized MCP sessions across
turns. That would also extend socket and session ownership through interruption,
scope expiry and Stop. Reusing only verified trust configuration removes the
observed repeated setup cost while retaining the current per-read teardown model.
Cold certificate loading remains synchronous; this change does not promise a hard
first-read deadline.

Recall now requires the gateway's per-result `_meta["starlight.memory.projection"]`
binding with schema `starlight.memory.projection-result.v1`, lowercase 64-character
projection/atom digests and an approved summary-only policy. The client validates
that report and constructs its own bounded watermark. Tool content cannot replace
those fields. Missing, malformed or unsupported bindings fail closed, including
legacy replies that only offered an earlier sync watermark. The two typed recall
tools return an unavailable result without a reference on denial.

The status RPC before recall is removed. The reader makes one actual recall call
after initialization, using the result's reported snapshot even when it differs
from a previously observed status. This removes one network call; no end-to-end
latency or RAM improvement has been measured. `observed_before_recall` is false
and `result_binding_validated` is true for accepted metadata. The client keeps
`recall_snapshot_verified` false because parsing reported digests does not verify
manifest signatures, atom bytes or the deployed gateway's atomic snapshot loader.
Gateway-owned signature verification and immutable per-result capture are
implemented in [Memory draft PR 29](https://github.com/frankxai/starlight-memory/pull/29).
Deployment and live recall acceptance remain open.
Queries must fit the gateway's 512 UTF-16 code-unit limit, including padding;
malformed Unicode is rejected before spending a tool call.

Seventeen binding cases fail against the exact prior reader source: four valid
result/policy/transport combinations were labelled with the earlier watermark,
and thirteen missing/malformed bindings were accepted. The repaired reader and
broker pass 113 focused checks with synthetic HTTP transports, including both
typed recall tools, refusal without reference disclosure, JSON/SSE, forged top-level
claims and owned-session cleanup. These tests start no HTTP gateway or provider;
metadata shape validation is separate from signed-memory runtime acceptance.

Both paths request MCP 2025-11-25 and accept the negotiated 2025-03-26,
2025-06-18 or 2025-11-25 version, sending its header on subsequent requests even
for stateless servers. Unknown versions stop before initialization notification
or tool access. Later responses cannot replace the session assigned at initialize.
Timeout or caller cancellation attempts cancellation of its own in-flight request
and DELETE within one additional 150 ms cleanup budget; initialize itself is never
cancelled by a protocol notification. The cancellation notification has a 75 ms
subdeadline so a stalled notification leaves time to attempt the owned-session
DELETE. Cleanup remains best effort and preserves
caller cancellation. See the official [lifecycle](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle),
[HTTP transport](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)
and [ping](https://modelcontextprotocol.io/specification/2025-11-25/basic/utilities/ping) contracts.

For a later authorized start, install the lightweight `./sidecar[native]` extra,
configure a server `XAI_API_KEY` or `OPENAI_API_KEY`, and run the existing dashboard
command. The server loads its local environment only on explicit startup. The
native path requires neither Pipecat nor local model/audio-device wheels. No RAM
claim follows until a real browser/session is measured.

## Turn observations

Grok's PCM interruption accounting uses `AudioContext.getOutputTimestamp()` for
the browser's output-frame position, rather than the processing graph's
`currentTime`. Per-content sample counts exclude gaps between chunks. A queued
content part that has not reached the output position is truncated at zero;
each pending item/content pair gets its own offset. Fully rendered timing
segments collapse into a frame counter, preserving offsets across gaps or
interleaved content. The browser retains at most 256 source nodes, 256 pending
timing segments and 128 content counters, in addition to the existing three-second
graph scheduling horizon. Limits are checked before allocating another source.
Completed-node disconnect failures remain owned for cleanup retry.

The [Web Audio output timestamp contract](https://www.w3.org/TR/webaudio/#dom-audiocontext-getoutputtimestamp)
distinguishes the device frame from graph processing. That timestamp is still a
browser estimate, not a microphone loopback or proof that a person heard sound.
Missing, throwing, nonfinite, future or backwards output clocks produce no
invented playback offset: audio is cleared and the session offers reconnect.
No polling worker or local model was added. The configured Grok server VAD
cancels generation itself, so barge-in sends content truncation without a
redundant manual cancellation, following the
[provider event contract](https://docs.x.ai/developers/rest-api-reference/inference/voice).
Device delay, truncation acceptance, voice quality and runtime RAM remain live
validation gates. These offsets are not promoted into verified turn receipts.

The browser submits enum/count/timing metadata to the same-origin, capability-gated
`/voice/observation` route. The local append-only file lives beside the existing run
ledger in ignored `memory/voice/`, with at most 100 records per session and a 32 MiB
storage cap. Repeating the same sequence is idempotent; replacing it is refused.
Cooperating local recorder processes use a nonblocking OS file lock around the
opened file's byte-count check and complete binary append. Windows uses a one-byte
`msvcrt` lock; POSIX uses `flock`. Contention reports unavailable through the
existing retry path instead of waiting on the other writer. Binary LF records
keep the byte cap exact on Windows as well. Short writes finish under the same
lock; failures close the owned descriptor. Existing history is never rotated or
truncated, and there is no lock file to expire or delete. Three regressions failed
before repair: competing processes spending the last record, ignoring another
process's lock, and Windows newline expansion exceeding the cap.

This protects cooperating writers on the local file; unrelated writes, external
file replacement and network-filesystem lock behavior are not established.
OS write completion does not prove power-loss durability. Nonblocking locks avoid
lock waits, while filesystem operations still have no hard latency deadline.
Storage failure is reported, leaves the record retryable, and preserves existing
history. Stop closes media first, lets bounded pending metadata writes finish,
then releases the local capability.

`voice.turn-observation.v1` is an observation format, not the proposed completed
Voice Edge receipt. Provider configuration and cumulative tool result counts come
from the broker. Browser status, token counts and durations are explicitly
unverified. Grok records a first scheduled PCM estimate relative to detected
speech end. WebRTC leaves that estimate unknown. Both retain unknown audible
latency, dollars and quality verdicts; neither transport proves actual per-turn
speaker output. Raw transcripts, audio, provider response IDs, tool arguments and
credentials are excluded. The recent-run read tool returns sanitized observation
summaries with acceptance unverified. These records support later validation;
they do not satisfy the conversation quality or latency gate by themselves.

## Embedding the native client

`dashboard/native-conversation.mjs` exports the transport without automatically
mounting controls or requesting the microphone. The existing cockpit mounts it
explicitly. An embedding page may set `brokerPrefix` and `workletPath` to local,
same-origin paths. External origins, query strings, encoded paths, traversal and
unknown broker actions are refused. This routing option does not add a gateway,
task queue or authentication authority.

`mountNativeConversation(document, options)` returns the conversation immediately,
with `mountReady` for capability discovery and `dispose()` for page unmount.
Disposal aborts discovery, removes its control and lifecycle listeners, clears
captions and stops owned audio and session authority. It also handles discovery
that returns after disposal. React or another host must call disposal in its
unmount cleanup. Command Center implements its existing `/voice` page and fixed
same-origin broker relay in [draft PR 70](https://github.com/frankxai/starlight-command-center/pull/70).
Its three-width mission journey uses synthetic devices and the existing fixture
proposal endpoint. Deployment and real audio acceptance remain open.

## Published cost comparison

Direct-provider rates checked on 10 October 2026; OpenRouter rates added on
11 October 2026. Taxes, account credits, subscription minimums,
tools and text-model usage can change the total. No invoice or live usage is proved.

| Route | Published unit price | What it covers |
| --- | --- | --- |
| Grok native conversation | $0.08 per provider-billed minute, plus $0.004 per text input | Native speech-to-speech; minute-metering semantics still need verification |
| ElevenLabs v4 Turbo | $0.011 per 1,000 characters until October 12; regular $0.04 | Generated speech only |
| ElevenLabs v3 Conversational or Flash/Turbo | $0.04 per 1,000 characters | Generated speech only |
| ElevenLabs Scribe v2 Realtime | $0.39 per input hour | Streaming transcription only |
| OpenRouter Eleven v4 Turbo | $0.020 per 1,000 characters through October 19 at 8am PT | Generated speech only; the displayed rate already includes the launch discount |
| OpenRouter Scribe v2 | $0.000031 per input second through the same launch offer | Recorded-audio REST transcription; realtime WebSocket Scribe is not offered on this route |
| OpenAI gpt-realtime-2.1 | Audio input $32/output $64 per million tokens; text input $4/output $24 | Modality-specific token usage |

Sources: [Grok pricing](https://docs.x.ai/developers/pricing),
[ElevenLabs API pricing](https://elevenlabs.io/pricing/api),
[OpenAI pricing](https://developers.openai.com/api/docs/pricing).
OpenRouter sources: [v4 Turbo price](https://openrouter.ai/elevenlabs/eleven-v4-turbo),
[Scribe v2 price](https://openrouter.ai/elevenlabs/scribe-v2) and
[launch offer and transport scope](https://openrouter.ai/blog/announcements/elevenlabs-on-openrouter/).

The public OpenRouter catalogue uses `output_modalities=speech` for these TTS
models; `audio` and the unfiltered chat catalogue do not list them. The live
speech catalogue and model endpoint returned v4 Turbo's $0.000020 per-character
price. Apply no second 50% reduction to that price. Catalogue visibility does not
establish account authentication or inference entitlement.

At the published OpenRouter offer rates, 10,000 generated characters plus 1,200
recorded input seconds calculate to $0.2372 before text inference, tools, account
fees and taxes. This includes batch transcription, not streaming Scribe. Its
latency and interruption behavior are not interchangeable with the native route.

For an illustrative workload with 10,000 generated characters and 20 minutes of
streaming transcription, v4 Turbo plus Scribe costs $0.24 during the promotion or
$0.53 at the regular rate, before text inference, tools and account minimums.
If Grok bills 20 minutes and 10 text inputs, its published rates give $1.64.
That billed-minute quantity is an assumption: the inspected pricing page does
not establish how connected time, silence or input/output speech map to minutes.
Neither connected duration nor summed input/output audio proves the billed total.
These are separate usage examples, not a matched quality or billing comparison.
Confirm metering and actual account usage before estimating savings. Do not select
the permanent provider on the short promotion or advertise a TTS-only total as the
full conversation cost.

Keep Ara/Orion native conversation as the accepted primary choice, OpenAI as the
separate alternative and ElevenLabs as the explicit account-voice component path.
Select a deep male ElevenLabs voice by listening to the account's licensed choices;
no voice ID or preference is inferred. Vendor TTS latency figures exclude capture,
turn detection, retrieval, text inference, network and playback. The matched live
comparison remains necessary before selecting a quality/latency winner.

## Browser component ownership

The dashboard adds an optional `elevenlabs` component choice, using the configured
Groq/OpenRouter/ElevenLabs graph. `voice-browser` installs Pipecat's WebRTC extra,
including aiortc and codec/OpenCV dependencies, plus its core ONNX runtime. It
does not install PyAudio, a local text model or a new daemon. Browser audio travels
to the existing server; local Silero detects turns there, then cloud services
transcribe, generate text and synthesize speech. Native Grok/OpenAI is the serious
alternative with fewer server audio dependencies. Session RAM remains unmeasured.

The browser component requires Pipecat 1.12 or newer. Its public detector cleanup
releases the analysis executor; 1.3's analyzer cleanup is a no-op. Compatibility
tests on 1.3 use a deterministic marker detector and do not establish the older
runtime's resource suitability. See the pinned
[1.12 VAD controller](https://github.com/pipecat-ai/pipecat/blob/v1.12.0/src/pipecat/audio/vad/vad_controller.py)
and [analyzer](https://github.com/pipecat-ai/pipecat/blob/v1.12.0/src/pipecat/audio/vad/vad_analyzer.py).

Capability discovery checks package/configuration presence without importing the
SDK or starting media. `/voice/session` reserves the existing broker's five-minute,
20-call read scope. `/voice/component-offer` accepts one audio SDP offer, at most
one application channel, 48 KiB of ASCII SDP and 64 candidates. The same-origin
guard and four-request limit apply. SDP stays in memory, with only its hash used
for duplicate-offer identity. Secrets stay on the server; the browser gets the
scope capability and SDP answer. No external STUN/TURN service is configured, so
this transport is intended for the existing local connection.

The server creates one owned async loop only on a configured Start. Requests
submit work to that persistent loop; returning an HTTP response does not cancel
the pipeline. Graph startup is bounded to ten seconds. The client gathers ICE
for at most eight seconds and allows 25 seconds for component connection readiness.
The negotiated `pipecat` channel sends heartbeat messages and bounded reply
captions. Mute and Stop retain the existing controls and recovery behavior.
Component turns do not write native-provider observation receipts.

The same read capability and call budget enter the graph. Generic native Stop
cannot release a component reservation. Component Stop first revokes reads,
cancels owned startup, then closes pipeline and peer with independent cleanup
jobs. Startup cancellation has a two-second wait allowance. If its own cleanup
is still pending, Stop returns a retryable failure and retains the same startup
task, runtime and reservation. A retry waits for that task without cancelling
its cleanup again; runtime close and slot release follow only after it finishes.
This uses [asyncio.wait](https://docs.python.org/3.12/library/asyncio-task.html#asyncio.wait),
which returns pending tasks without adding timeout cancellation. The allowance
does not guarantee scheduler timing or completion of SDK resources.
Owned cleanup failures are consumed even if the last Stop caller disconnects,
so an unobserved task does not log a raw resource exception. The failed job and
its handles remain owned; consuming the exception does not mark cleanup complete.
Failure holds the reservation and retained handles for Retry cleanup.
Expiry also retries cleanup; it cannot silently admit a replacement native
session while component resources remain. The async host loop persists after
ordinary Stop and joins when the existing server closes. A failed host shutdown
keeps its owner and handles for another shutdown attempt.

Tests separate pure authority/cleanup fixtures, browser transport fixtures and
actual SDK peer negotiation. Hosted tests use deterministic services and local
peers to verify SDP, captions and generated marker PCM across the audio codec.
They use no provider inference or audio device and do not establish
authentication, audible interruption, preferred
voice, cost, latency or RAM. No local app or voice session was started for this
implementation. Independent revision review and an admitted live comparison
remain release requirements.

## Remaining work

The standalone Groq/OpenRouter/ElevenLabs graph now binds optional Silero voice
detection to `LLMUserAggregatorParams` when local audio is selected. Groq's
segmented STT requires VAD start/stop events. The former graph supplied neither
an analyzer nor working shell push-to-talk events, so constructing its services
could succeed without producing a transcription turn. Both supported SDK versions
already include the analyzer and ONNX runtime; no extra package is required.
Headless construction and native provider conversation keep the analyzer unloaded.
The SDK regression checks marker
PCM through actual VAD control, segmented STT and user-context aggregation using
deterministic services, with no model, provider or audio device. It establishes
frame wiring, not Silero accuracy, audible interruption, live latency or RAM.
The standalone browser component transport now connects this graph through the
existing dashboard broker. Command Center relay adoption and live audio remain
separate acceptance work.

The legacy text dispatcher now follows the same execution boundary as the native
and component tools. It returns handoff previews and append-only audit outcomes;
it cannot launch a CLI. `--live` returns `unavailable` with a nonzero exit code,
while always-ask requests remain blocked. Every packet is marked preview and
unadmitted, and every risk tier still requires admission. Spoken text confirms
that no harness has started. Retrying preserves historical audit records without
creating pending approval or queued work. This closes an unmanaged launch path;
it does not implement managed execution or establish live acceptance.

Select and listen to an account voice through the ElevenLabs component pipeline;
connect durable dispatch/approval and verified memory deployment; add trusted
playback, billing and reviewed quality evidence to turn receipts. Verify real
provider authentication and audio interruption. Browser
appearance, touch and keyboard behavior need inspection under an authorized runtime
or preview. Compare native conversation and GPT Live delegation on matched tasks.
Obtain independent revision review before release and measure
first playable audio, session closure, provider cost and resource use under admitted
runtime conditions. Keep this implementation distinct from a released daily driver.
