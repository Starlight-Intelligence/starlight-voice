import test from "node:test";
import assert from "node:assert/strict";
import { NativeConversation, PcmPlayback, ToolBatch, TurnObservations, decodePcm16, mountNativeConversation, pcm16Base64 } from "../native-conversation.mjs";

const flush = () => new Promise((resolve) => setImmediate(resolve));
const deferred = () => { let resolve; const promise = new Promise((r) => { resolve = r; }); return { promise, resolve }; };

for (const failing of ["track", "channel", "peer", "audio", "playback", "capture", "context"]) {
  test(`Stop keeps releasing independent resources after ${failing} throws`, async () => {
    const attempted = [], requests = [], states = [];
    const close = (name) => { attempted.push(name); if (name === failing) throw new Error("private device detail"); };
    const conversation = new NativeConversation({ onState: (state) => states.push(state), fetchFn: async (path) => {
      requests.push(path); return { ok: true, json: async () => ({ ok: true }) };
    } });
    conversation.active = true;
    conversation.session = { session_id: "old-session", capability: "old-cap" };
    conversation.stream = { getTracks: () => [{ enabled: true, stop: () => close("track") }] };
    conversation.channel = { close: () => close("channel") };
    conversation.peer = { close: () => close("peer") };
    conversation.audio = { pause: () => close("audio"), srcObject: {} };
    conversation.playback = { interrupt: () => close("playback") };
    conversation.capture = { port: {}, disconnect: () => close("capture") };
    conversation.context = { close: () => close("context") };
    await conversation.stop();
    assert.deepEqual(attempted, ["track", "channel", "peer", "audio", "playback", "capture", "context"]);
    assert.deepEqual(requests, ["/voice/stop"]);
    assert.equal(conversation.cleanupFailed, true);
    assert.equal(states.at(-1).error, true);
    assert.ok(!states.at(-1).label.includes("private"));
  });
}

test("rejected audio closure blocks new capture until an explicit retry releases it", async () => {
  let fails = true, captures = 0, closes = 0;
  const states = [];
  const conversation = new NativeConversation({ onState: (state) => states.push(state),
    media: { getUserMedia: async () => { captures++; throw new Error("Unexpected capture"); } },
  });
  conversation.context = { close: async () => { closes++; if (fails) throw new Error("private close failure"); } };
  await conversation.stop();
  assert.equal(conversation.cleanupFailed, true);
  assert.match(states.at(-1).label, /retry cleanup/i);
  await conversation.start("openai", "marin");
  assert.equal(captures, 0);
  fails = false;
  await conversation.stop();
  assert.equal(closes, 2);
  assert.equal(conversation.cleanupFailed, false);
  assert.equal(states.at(-1).error, false);
});

test("new Start cannot overlap pending Stop or ask for another microphone", async () => {
  const closing = deferred(); let captures = 0;
  const conversation = new NativeConversation({ media: { getUserMedia: async () => {
    captures++; throw new Error("Unexpected capture");
  } } });
  conversation.context = { close: () => closing.promise };
  const stopped = conversation.stop();
  try {
    await conversation.start("openai", "marin");
    assert.equal(captures, 0);
    assert.equal(conversation.stopping, true);
  } finally { closing.resolve(); await stopped; }
});

test("stalled context close cannot prevent bounded Stop and old-session release", async () => {
  const closing = deferred(), requests = []; let timer;
  const conversation = new NativeConversation({ fetchFn: async (path) => {
    requests.push(path); return { ok: true, json: async () => ({ ok: true }) };
  } });
  conversation.context = { close: () => closing.promise };
  conversation.session = { session_id: "old-session", capability: "old-cap" };
  const stopped = conversation.stop();
  try {
    const result = await Promise.race([stopped.then(() => "stopped"), new Promise((resolve) => {
      timer = setTimeout(() => resolve("blocked"), 1400);
    })]);
    assert.equal(result, "stopped");
    assert.equal(conversation.cleanupFailed, true);
    assert.deepEqual(requests, ["/voice/stop"]);
  } finally { clearTimeout(timer); closing.resolve(); await stopped; }
  await flush();
  assert.equal(conversation.cleanupFailed, false);
});

test("a late rejected play promise cannot stop a replacement session", async () => {
  let peer, rejectPlay;
  class Peer {
    constructor() { peer = this; }
    addTrack() {}
    createDataChannel() { return { readyState: "open", close() {} }; }
    async createOffer() { return { sdp: "fixture-offer" }; }
    async setLocalDescription() {}
    async setRemoteDescription() {}
    close() {}
  }
  const play = new Promise((_resolve, reject) => { rejectPlay = reject; });
  const conversation = new NativeConversation({ PeerClass: Peer,
    audioFactory: () => ({ play: () => play, pause() {} }),
    fetchFn: async (path) => path.startsWith("https:") ? { ok: true, text: async () => "fixture-answer" }
      : { ok: true, json: async () => ({ ok: true }) },
  });
  conversation.active = true; conversation.epoch = 1; conversation.abort = new AbortController();
  conversation.stream = { getTracks: () => [] };
  conversation.session = { session_id: "old", capability: "old-cap", client_secret: { value: "fixture-token" } };
  await conversation.connectOpenAI(1);
  peer.ontrack({ streams: [{}] });
  await conversation.stop();
  conversation.epoch++; conversation.active = true;
  conversation.session = { session_id: "replacement", capability: "replacement-cap" };
  try {
    rejectPlay(new Error("Playback was denied after Stop")); await flush();
    assert.equal(conversation.active, true);
    assert.equal(conversation.session.session_id, "replacement");
  } finally { await conversation.stop(); }
});

test("mounted cleanup states keep Start blocked and expose an explicit retry", async () => {
  const page = fakeVoiceDocument(); let fails = true;
  const conversation = mountNativeConversation(page.doc, { lifecycleTarget: page.lifecycle,
    fetchFn: async () => ({ ok: true, json: async () => ({ providers: [{ id: "grok", configured: true, voices: ["ara"] }] }) }),
  });
  await conversation.mountReady;
  conversation.context = { close: async () => { if (fails) throw new Error("close denied"); } };
  await conversation.stop();
  assert.equal(page.elements.voiceStart.disabled, true);
  assert.equal(page.elements.voiceStop.disabled, false);
  assert.equal(page.elements.voiceStop.textContent, "Retry cleanup");
  fails = false; await conversation.stop();
  assert.equal(page.elements.voiceStart.disabled, false);
  assert.equal(page.elements.voiceStop.textContent, "Stop");
  await conversation.dispose();
});

test("Stop attempts every track even when the first one fails", async () => {
  const stopped = [], tracks = [0, 1].map((index) => ({ enabled: true, stop() {
    stopped.push(index); if (index === 0) throw new Error("track failure");
  } }));
  const conversation = new NativeConversation();
  conversation.stream = { getTracks: () => tracks };
  await conversation.stop();
  assert.deepEqual(stopped, [0, 1]);
  assert.ok(tracks.every((track) => track.enabled === false));
});

test("parallel Stop calls share cleanup and release the captured session once", async () => {
  const closing = deferred(), requests = []; let closes = 0;
  const conversation = new NativeConversation({ fetchFn: async (path) => {
    requests.push(path); return { ok: true, json: async () => ({ ok: true }) };
  } });
  conversation.context = { close: () => { closes++; return closing.promise; } };
  conversation.session = { session_id: "old", capability: "old-cap" };
  const first = conversation.stop(), second = conversation.stop();
  try { assert.equal(first, second); await flush(); assert.equal(closes, 1); }
  finally { closing.resolve(); await Promise.all([first, second]); }
  assert.deepEqual(requests, ["/voice/stop"]);
});

test("late discovery cannot re-enable Start over a failed audio cleanup", async () => {
  const page = fakeVoiceDocument(), discovery = deferred(); let fails = true;
  const conversation = mountNativeConversation(page.doc, { lifecycleTarget: page.lifecycle, fetchFn: () => discovery.promise });
  conversation.context = { close: async () => { if (fails) throw new Error("close failed"); } };
  await conversation.stop();
  discovery.resolve({ ok: true, json: async () => ({ providers: [{ id: "grok", configured: true, voices: ["ara"] }] }) });
  await conversation.mountReady;
  assert.equal(page.elements.voiceStart.disabled, true);
  assert.equal(page.elements.voiceStop.textContent, "Retry cleanup");
  fails = false; await conversation.stop(); await conversation.dispose();
});

test("a failing status renderer cannot prevent media cleanup or authority release", async () => {
  const closed = [], requests = [];
  const conversation = new NativeConversation({ onState: () => { throw new Error("render failed"); },
    fetchFn: async (path) => { requests.push(path); return { ok: true, json: async () => ({ ok: true }) }; },
  });
  conversation.stream = { getTracks: () => [{ stop: () => closed.push("track") }] };
  conversation.session = { session_id: "old", capability: "old-cap" };
  await conversation.stop();
  assert.deepEqual(closed, ["track"]);
  assert.deepEqual(requests, ["/voice/stop"]);
  assert.equal(conversation.stopping, false);
});

test("stalled metadata cannot keep Stop waiting after its flush allowance", async () => {
  const write = deferred(), requests = []; let timer;
  const conversation = new NativeConversation({ fetchFn: async (path) => {
    requests.push(path); return { ok: true, json: async () => ({ ok: true }) };
  } });
  conversation.session = { session_id: "old", capability: "old-cap" };
  conversation.observationWrites = new Set([write.promise]);
  conversation.observationAbort = new AbortController();
  const abort = conversation.observationAbort;
  const stopped = conversation.stop();
  try {
    const result = await Promise.race([stopped.then(() => "stopped"), new Promise((resolve) => {
      timer = setTimeout(() => resolve("blocked"), 2400);
    })]);
    assert.equal(result, "stopped"); assert.equal(abort.signal.aborted, true);
    assert.deepEqual(requests, ["/voice/stop"]);
  } finally { clearTimeout(timer); write.resolve(); await stopped; }
});

test("PCM interruption attempts later sources after an earlier disconnect fails", () => {
  const attempted = [], playback = new PcmPlayback({ currentTime: 0 }); let fails = true;
  const first = { stop: () => attempted.push("first-stop"), disconnect() {
    attempted.push("first-disconnect"); if (fails) throw new Error("disconnect failed");
  } };
  const second = { stop: () => attempted.push("second-stop"), disconnect: () => attempted.push("second-disconnect") };
  playback.nodes.add(first); playback.nodes.add(second);
  assert.throws(() => playback.interrupt(), /cleanup/);
  assert.deepEqual(attempted, ["first-stop", "first-disconnect", "second-stop", "second-disconnect"]);
  assert.equal(playback.nodes.size, 1);
  fails = false; playback.interrupt(); assert.equal(playback.nodes.size, 0);
});

test("default browser fetch keeps the global receiver for broker requests", async () => {
  const original = globalThis.fetch;
  const requests = [];
  globalThis.fetch = async function (path, options) {
    assert.equal(this, globalThis, "browser fetch requires its global receiver");
    requests.push({ path, options });
    return { ok: true, json: async () => ({ ok: true }) };
  };
  try {
    const conversation = new NativeConversation({ brokerPrefix: "/api/voice/native" });
    await conversation.fetchFn(conversation.endpoint("capabilities"), { cache: "no-store" });
    await conversation.post("/voice/session", { provider: "grok", voice: "ara" });
    assert.deepEqual(requests.map(request => request.path), ["/api/voice/native/capabilities", "/api/voice/native/session"]);
  } finally { globalThis.fetch = original; }
});

test("PCM handles clamping, signed little-endian and malformed chunks", () => {
  const samples = decodePcm16(pcm16Base64(new Float32Array([-2, -1, 0, 0.5, 1, 2])));
  assert.deepEqual([...samples].slice(0, 3), [-1, -1, 0]);
  assert.ok(Math.abs(samples[3] - 0.5) < 1 / 32768);
  assert.equal(samples[4], 32767 / 32768);
  assert.equal(samples[5], samples[4]);
  assert.throws(() => decodePcm16("AA=="));
  assert.throws(() => decodePcm16("!invalid"));
  assert.throws(() => decodePcm16("A".repeat(128 * 1024 + 1)));
});

test("parallel tools deliver both results before one response.create; duplicate ID is ignored", async () => {
  const first = deferred(), second = deferred(), sent = [], executed = [];
  const tools = new ToolBatch((id) => { executed.push(id); return id === "a" ? first.promise : second.promise; }, (event) => sent.push(event));
  tools.add({ call_id: "a", name: "node_health", arguments: "{}" });
  tools.add({ call_id: "b", name: "portfolio_status", arguments: "{}" });
  tools.add({ call_id: "a", name: "node_health", arguments: "{}" });
  const done = tools.done();
  first.resolve({ status: "ok" }); await flush();
  assert.equal(sent.length, 1);
  assert.equal(sent[0].item.type, "function_call_output");
  second.resolve({ status: "unavailable" }); await done;
  assert.deepEqual(executed, ["a", "b"]);
  assert.equal(sent.at(-1).type, "response.create");
  assert.equal(sent.filter((event) => event.type === "response.create").length, 1);
  await tools.done(); assert.equal(sent.length, 3);
});

test("tool errors are explicit; cancelled turn does not start another response", async () => {
  const sent = [];
  const tools = new ToolBatch(() => { throw new Error("private detail"); }, (event) => sent.push(event));
  tools.add({ call_id: "a", name: "node_health", arguments: "{}" });
  await tools.done(false);
  assert.equal(sent.length, 1);
  assert.match(sent[0].item.output, /unavailable/);
  assert.ok(!sent[0].item.output.includes("private detail"));
});

for (const change of ["interruption", "replacement response", "replacement session", "stopped session", "unchanged turn"]) {
  test(`pending tool continuation respects ${change}`, async () => {
    const pending = deferred(), sent = [];
    const conversation = new NativeConversation();
    conversation.active = true;
    conversation.tools = new ToolBatch(() => pending.promise, (event) => sent.push(event));
    conversation.event({ type: "response.created", response: { id: "original" } });
    conversation.event({ type: "response.function_call_arguments.done", response_id: "original", call_id: "call", name: "node_health", arguments: "{}" });
    conversation.event({ type: "response.done", response: { id: "original", status: "completed" } });
    await flush();
    assert.equal(sent.length, 0);
    if (change === "interruption") conversation.event({ type: "input_audio_buffer.speech_started" });
    if (change === "replacement response") {
      conversation.event({ type: "input_audio_buffer.speech_started" });
      conversation.event({ type: "response.created", response: { id: "replacement" } });
    }
    if (change === "replacement session") conversation.epoch++;
    if (change === "stopped session") conversation.active = false;
    pending.resolve({ status: "ok" });
    await flush();
    assert.equal(sent.filter((event) => event.type === "response.create").length, change === "unchanged turn" ? 1 : 0);
  });
}

function fakeContext() {
  const sources = [];
  return { currentTime: 0, destination: {}, sources,
    createBuffer: (_, count, rate) => ({ duration: count / rate, copyToChannel() {} }),
    createBufferSource() { const source = { connect() {}, disconnect() { this.disconnected = true; }, start(time) { this.startTime = time; }, stop() { this.stopped = true; } }; sources.push(source); return source; },
  };
}

test("interruption clears all buffered sources and reports only audio heard", () => {
  const context = fakeContext(), player = new PcmPlayback(context);
  const chunk = pcm16Base64(new Float32Array(480));
  player.enqueue(chunk, "item-a"); player.enqueue(chunk, "item-a");
  context.currentTime = 10;
  const result = player.interrupt();
  assert.equal(result.itemId, "item-a");
  assert.ok(result.heardMs >= 19 && result.heardMs <= 20);
  assert.ok(context.sources.every((source) => source.stopped && source.disconnected));
  assert.equal(player.nodes.size, 0);
  assert.equal(player.nextTime, 0);
});

test("playback backlog is bounded", () => {
  const player = new PcmPlayback(fakeContext());
  player.nextTime = 3.1;
  assert.throws(() => player.enqueue(pcm16Base64(new Float32Array(480)), "item"), /fell behind/);
});

test("permission arriving after Stop cannot open a broker session", async () => {
  const permission = deferred(); let stopped = 0, fetches = 0;
  const conversation = new NativeConversation({ media: { getUserMedia: () => permission.promise }, fetchFn: () => { fetches++; } });
  const start = conversation.start("openai", "marin");
  await conversation.stop();
  const track = { stop: () => stopped++ };
  permission.resolve({ getTracks: () => [track] });
  await start;
  assert.equal(stopped, 1); assert.equal(fetches, 0); assert.equal(conversation.active, false);
});

test("permission rejection produces a recoverable state", async () => {
  const states = [];
  const conversation = new NativeConversation({ onState: (state) => states.push(state), media: {
    getUserMedia: () => Promise.reject(Object.assign(new Error("denied"), { name: "NotAllowedError" })),
  } });
  await conversation.start("openai", "marin");
  assert.equal(conversation.active, false);
  assert.match(states.at(-1).label, /permission was denied/);
});

test("Stop releases owned media, audio, channel, peer, worklet and local authority", async () => {
  const closed = [], requests = [], track = { enabled: true, stop: () => closed.push("track") };
  const conversation = new NativeConversation({ fetchFn: async (path, options) => {
    requests.push({ path, options }); return { ok: true, json: async () => ({ ok: true }) };
  } });
  conversation.active = conversation.ready = true;
  conversation.abort = new AbortController();
  conversation.session = { provider: "grok", session_id: "session", capability: "cap" };
  conversation.stream = { getTracks: () => [track], getAudioTracks: () => [track] };
  conversation.channel = { readyState: 1, bufferedAmount: 0, send() {}, close: () => closed.push("channel") };
  conversation.peer = { close: () => closed.push("peer") };
  conversation.audio = { pause: () => closed.push("audio"), srcObject: {} };
  conversation.context = { close: async () => closed.push("context") };
  conversation.capture = { port: {}, disconnect: () => closed.push("worklet") };
  conversation.mute(); assert.equal(track.enabled, false);
  await conversation.stop();
  assert.deepEqual(closed, ["track", "channel", "peer", "audio", "worklet", "context"]);
  assert.equal(requests[0].path, "/voice/stop");
  assert.equal(requests[0].options.headers["X-Voice-Capability"], "cap");
  assert.equal(conversation.session, null); assert.equal(conversation.stream, null);
});

test("barge-in discards late PCM and cancels/truncates the response", () => {
  const sent = []; let queued = 0;
  const conversation = new NativeConversation();
  conversation.active = true; conversation.responding = true;
  conversation.session = { provider: "grok" };
  conversation.channel = { readyState: 1, bufferedAmount: 0, send: (event) => sent.push(JSON.parse(event)) };
  conversation.playback = { interrupt: () => ({ itemId: "item", heardMs: 123 }), enqueue: () => queued++ };
  conversation.event({ type: "input_audio_buffer.speech_started" });
  conversation.event({ type: "response.output_audio.delta", delta: "invalid" });
  assert.equal(queued, 0);
  assert.deepEqual(sent.map((event) => event.type), ["response.cancel", "conversation.item.truncate"]);
  assert.equal(sent[1].audio_end_ms, 123);
});

test("upload backlog terminates submission instead of accumulating audio", () => {
  const conversation = new NativeConversation(); conversation.active = true; conversation.session = { provider: "grok" };
  conversation.channel = { readyState: 1, bufferedAmount: 2 * 1024 * 1024, send: () => assert.fail("must not send") };
  assert.throws(() => conversation.send({ type: "input_audio_buffer.append", audio: "sample" }), /fell behind/);
});

test("no browser globals or microphone calls are required to import the module", () => {
  assert.equal(typeof NativeConversation, "function");
});

test("Grok connects with an ephemeral subprotocol and gates mic until session.updated", async () => {
  const sent = [], requests = [], contexts = [], sockets = [], worklets = [];
  const track = { enabled: true, stop() {} }, stream = { getTracks: () => [track], getAudioTracks: () => [track] };
  class Context {
    constructor() { Object.assign(this, fakeContext()); this.sampleRate = 48000; contexts.push(this); this.audioWorklet = { addModule: async (path) => { this.workletPath = path; } }; }
    async resume() {}
    async close() { this.closed = true; }
    createMediaStreamSource() { return { connect() {}, disconnect() {} }; }
    createGain() { return { gain: {}, connect() {}, disconnect() {} }; }
  }
  class Socket {
    constructor(url, protocols) { this.url = url; this.protocols = protocols; this.readyState = 1; this.bufferedAmount = 0; sockets.push(this); }
    send(value) { sent.push(JSON.parse(value)); }
    close() { this.closed = true; }
  }
  class Worklet {
    constructor() { this.port = {}; worklets.push(this); }
    connect() {} disconnect() {}
  }
  const conversation = new NativeConversation({ media: { getUserMedia: async () => stream },
    AudioContextClass: Context, WebSocketClass: Socket, WorkletClass: Worklet, workletPath: "/native-voice/pcm-capture.js",
    fetchFn: async (path, options) => {
      requests.push({ path, options });
      return { ok: true, json: async () => path === "/voice/session" ? {
        provider: "grok", voice: "ara", model: "grok-voice-think-fast-2.0", session_id: "local-id", capability: "cap",
        client_secret: { value: "ephemeral-token" }, instructions: "server-owned", tools: [], max_session_seconds: 300,
      } : { ok: true } };
    },
  });
  await conversation.start("grok", "ara");
  assert.equal(contexts[0].workletPath, "/native-voice/pcm-capture.js");
  assert.equal(track.enabled, false); assert.equal(conversation.ready, false);
  assert.deepEqual(sockets[0].protocols, ["xai-client-secret.ephemeral-token"]);
  assert.ok(!("client_secret" in conversation.session));
  sockets[0].onopen();
  assert.equal(sent[0].session.reasoning.effort, "none");
  assert.equal(sent[0].session.audio.input.format.rate, 48000);
  worklets[0].port.onmessage({ data: new Float32Array(960) });
  assert.equal(sent.length, 1);
  sockets[0].onmessage({ data: JSON.stringify({ type: "session.updated" }) });
  assert.equal(track.enabled, true);
  worklets[0].port.onmessage({ data: new Float32Array(960) });
  assert.equal(sent.at(-1).type, "input_audio_buffer.append");
  conversation.event({ type: "input_audio_buffer.speech_stopped" });
  conversation.event({ type: "response.created" });
  conversation.event({ type: "response.output_audio.delta", item_id: "provider-item", delta: "AQA=" });
  conversation.event({ type: "response.audio_transcript.delta", delta: "private transcript" });
  conversation.event({ type: "response.done", response: { status: "completed", usage: {
    input_tokens: 12, output_tokens: 3, details: "private detail",
  } } });
  await conversation.stop();
  const record = JSON.parse(requests.find((request) => request.path === "/voice/observation").options.body);
  assert.equal(record.observation.status, "completed");
  assert.ok(record.observation.first_scheduled_audio_ms >= 15);
  assert.deepEqual(record.observation.token_counts, { input_tokens: 12, output_tokens: 3 });
  assert.ok(!JSON.stringify(record).includes("private") && !JSON.stringify(record).includes("provider-item"));
  assert.equal(sockets[0].closed, true); assert.equal(contexts[0].closed, true);
  assert.equal(requests.at(-1).path, "/voice/stop");
});

test("OpenAI negotiates native WebRTC with the ephemeral key and cleans up", async () => {
  const requests = [], peers = [];
  const track = { enabled: true, stop() {} }, stream = { getTracks: () => [track], getAudioTracks: () => [track] };
  class Peer {
    constructor() { peers.push(this); }
    addTrack() {}
    createDataChannel() { this.dc = { readyState: "open", bufferedAmount: 0, send() {}, close() {} }; return this.dc; }
    async createOffer() { return { type: "offer", sdp: "mock-offer" }; }
    async setLocalDescription(value) { this.local = value; }
    async setRemoteDescription(value) { this.remote = value; }
    close() { this.closed = true; }
  }
  const conversation = new NativeConversation({ media: { getUserMedia: async () => stream }, PeerClass: Peer,
    audioFactory: () => ({ play: async () => {}, pause() {} }),
    fetchFn: async (path, options) => {
      requests.push({ path, options });
      if (path === "https://api.openai.com/v1/realtime/calls") return { ok: true, text: async () => "mock-answer" };
      return { ok: true, json: async () => path === "/voice/session" ? {
        provider: "openai", voice: "cedar", model: "gpt-realtime-2.1", session_id: "local-id", capability: "cap",
        client_secret: { value: "ephemeral-token" }, tools: [], max_session_seconds: 300,
      } : { ok: true } };
    },
  });
  await conversation.start("openai", "cedar");
  assert.equal(track.enabled, false);
  assert.equal(requests[1].options.headers.Authorization, "Bearer ephemeral-token");
  assert.equal(requests[1].options.headers["Content-Type"], "application/sdp");
  assert.deepEqual(peers[0].remote, { type: "answer", sdp: "mock-answer" });
  peers[0].dc.onopen(); assert.equal(track.enabled, true);
  assert.ok(!("client_secret" in conversation.session));
  conversation.event({ type: "input_audio_buffer.speech_stopped" });
  conversation.event({ type: "response.created" });
  conversation.event({ type: "response.done", response: { status: "completed" } });
  await conversation.stop(); assert.equal(peers[0].closed, true);
  const record = JSON.parse(requests.find((request) => request.path === "/voice/observation").options.body);
  assert.equal(record.observation.first_scheduled_audio_ms, null);
  assert.equal(record.observation.token_counts, null);
});

test("turn timing retains its original speech-end across interruption and next input", () => {
  let time = 100; const records = [];
  const observer = new TurnObservations((record) => records.push(record), () => time);
  observer.speechStopped(); time = 150; observer.created();
  time = 170; observer.scheduled(15);
  time = 180; observer.interrupt();
  time = 300; observer.speechStopped();
  time = 350; observer.done("cancelled");
  assert.deepEqual(records[0], { sequence: 1, status: "cancelled", interrupted: true,
    response_created_ms: 50, first_scheduled_audio_ms: 85, response_done_ms: 250, token_counts: null });
});

test("unsolicited output and malformed usage remain unknown rather than zero or dollars", () => {
  const records = [], observer = new TurnObservations((record) => records.push(record), () => 50);
  observer.created(); observer.scheduled(15); observer.done("completed", { input_tokens: true, output_tokens: -1 });
  assert.equal(records[0].response_created_ms, null);
  assert.equal(records[0].response_done_ms, null);
  assert.equal(records[0].first_scheduled_audio_ms, null);
  assert.equal(records[0].token_counts, null);
  assert.ok(!("cost" in records[0]) && !("quality" in records[0]));
  observer.done("completed"); assert.equal(records.length, 1);
});

test("turn count is bounded and overlapping responses preserve an unknown prior result", () => {
  const records = [], observer = new TurnObservations((record) => records.push(record), () => 0);
  observer.created(); observer.created();
  assert.equal(records[0].status, "unknown");
  observer.done("completed");
  for (let i = 2; i < 100; i++) { observer.created(); observer.done("completed"); }
  assert.equal(records.length, 100);
  assert.throws(() => observer.created(), /Turn limit reached/);
});

test("Stop closes media before waiting for metadata, then releases authority", async () => {
  const write = deferred(), closed = [], requests = [];
  const conversation = new NativeConversation({ fetchFn: async (path) => {
    requests.push(path); return { ok: true, json: async () => ({ ok: true }) };
  } });
  conversation.active = true;
  conversation.session = { session_id: "session", capability: "cap" };
  conversation.stream = { getTracks: () => [{ stop: () => closed.push("track") }] };
  conversation.observationWrites = new Set([write.promise]);
  const stopped = conversation.stop();
  await flush();
  assert.deepEqual(closed, ["track"]); assert.deepEqual(requests, []);
  write.resolve(); await stopped;
  assert.deepEqual(requests, ["/voice/stop"]);
});

test("late events from an older response cannot play or finish the current turn", () => {
  const records = [], conversation = new NativeConversation(); let played = 0;
  conversation.observations = new TurnObservations((record) => records.push(record), () => 100);
  conversation.playback = { enqueue: () => played++ };
  conversation.event({ type: "response.created", response: { id: "current" } });
  conversation.event({ type: "response.created", response: { id: "current" } });
  conversation.event({ type: "response.audio.delta", response_id: "old", delta: "invalid" });
  conversation.event({ type: "response.done", response: { id: "old", status: "completed" } });
  assert.equal(played, 0); assert.equal(records.length, 0); assert.equal(conversation.responding, true);
  conversation.event({ type: "response.done", response: { id: "current", status: "completed" } });
  assert.equal(records.length, 1); assert.equal(records[0].sequence, 1);
  assert.ok(!JSON.stringify(records[0]).includes("current"));
});

test("broker routing stays same-origin and forwards capabilities only to fixed actions", async () => {
  const requests = [], conversation = new NativeConversation({ brokerPrefix: "/api/voice/native", fetchFn: async (path, options) => {
    requests.push({ path, options }); return { ok: true, json: async () => ({ ok: true }) };
  } });
  for (const action of ["session", "tool", "observation", "stop"]) {
    await conversation.post(`/voice/${action}`, {}, { capability: "local-capability" });
  }
  assert.deepEqual(requests.map((request) => request.path), ["session", "tool", "observation", "stop"].map((action) => `/api/voice/native/${action}`));
  assert.ok(requests.every((request) => request.options.credentials === "same-origin" && request.options.redirect === "error" && request.options.headers["X-Voice-Capability"] === "local-capability"));
  await assert.rejects(conversation.post("https://example.test/voice/tool", {}), /Unknown/);
  await assert.rejects(conversation.post("/voice/arbitrary", {}), /Unknown/);
  assert.equal(requests.length, 4);
  for (const path of ["https://example.test", "//example.test", "/voice/../elsewhere", "/voice%2f", "/voice?q=1", "/voice#x", "/voice\\elsewhere"]) {
    assert.throws(() => new NativeConversation({ brokerPrefix: path }), /same-origin/);
  }
  for (const path of ["https://example.test/capture.js", "//example.test/capture.js", "/../capture.js", "/capture.js?q=1", "/capture%2ejs", "/capture.mjs"]) {
    assert.throws(() => new NativeConversation({ workletPath: path }), /same-origin/);
  }
});

function fakeVoiceDocument() {
  class Target {
    constructor() { this.events = new Map(); this.disabled = false; this.textContent = ""; this.children = []; this.value = ""; this.classList = { toggle() {} }; }
    addEventListener(name, handler) { const handlers = this.events.get(name) || new Set(); handlers.add(handler); this.events.set(name, handlers); }
    removeEventListener(name, handler) { this.events.get(name)?.delete(handler); }
    emit(name, event = {}) { for (const handler of [...(this.events.get(name) || [])]) handler(event); }
    append(child) { this.children.push(child); }
    replaceChildren() { this.children = []; }
    setAttribute() {}
    listenerCount() { return [...this.events.values()].reduce((total, handlers) => total + handlers.size, 0); }
  }
  const doc = new Target(), lifecycle = new Target();
  const ids = ["voiceStart", "voiceStop", "voiceMute", "voiceProvider", "voiceVoice", "voiceStatus", "voiceCaption"];
  const elements = Object.fromEntries(ids.map((id) => [id, new Target()]));
  doc.getElementById = (id) => elements[id]; doc.createElement = () => new Target();
  return { doc, lifecycle, elements, count: () => [doc, lifecycle, ...Object.values(elements)].reduce((total, target) => total + target.listenerCount(), 0) };
}

test("explicit mounting supports disposal and remount without stale page listeners or captions", async () => {
  const page = fakeVoiceDocument(), requests = [];
  const options = { lifecycleTarget: page.lifecycle, brokerPrefix: "/api/voice/native", fetchFn: async (path, settings) => {
    requests.push({ path, settings }); return { ok: true, json: async () => ({ providers: [{ id: "grok", configured: true, voices: ["ara", "orion"] }] }) };
  } };
  const first = mountNativeConversation(page.doc, options);
  await first.mountReady;
  assert.equal(requests[0].path, "/api/voice/native/capabilities");
  assert.equal(requests[0].settings.credentials, "same-origin");
  assert.equal(requests[0].settings.redirect, "error");
  assert.equal(page.elements.voiceStart.disabled, false);
  assert.equal(page.elements.voiceProvider.children.length, 1);
  assert.equal(page.elements.voiceVoice.children.length, 2);
  assert.equal(page.count(), 7);
  let released = 0;
  first.active = true; first.stream = { getTracks: () => [{ stop: () => released++ }] };
  first.onText("private caption");
  await first.dispose(); await first.dispose();
  assert.equal(released, 1); assert.equal(page.count(), 0);
  assert.equal(page.elements.voiceCaption.textContent, "");
  first.onText("late caption"); assert.equal(page.elements.voiceCaption.textContent, "");
  const second = mountNativeConversation(page.doc, options); await second.mountReady;
  assert.equal(page.elements.voiceProvider.children.length, 1); assert.equal(page.count(), 7);
  let starts = 0; second.start = async () => starts++;
  page.elements.voiceStart.emit("click"); assert.equal(starts, 1);
  await second.dispose(); assert.equal(page.count(), 0);
});

test("disposal aborts pending discovery and a late reply cannot re-enable an unmounted page", async () => {
  const page = fakeVoiceDocument(), discovery = deferred(); let signal;
  const conversation = mountNativeConversation(page.doc, { lifecycleTarget: page.lifecycle, fetchFn: (_, options) => { signal = options.signal; return discovery.promise; } });
  await conversation.dispose(); assert.equal(signal.aborted, true); assert.equal(page.count(), 0);
  discovery.resolve({ ok: true, json: async () => ({ providers: [{ id: "grok", configured: true, voices: ["ara"] }] }) });
  await conversation.mountReady;
  assert.equal(page.elements.voiceStart.disabled, true); assert.equal(page.elements.voiceProvider.children.length, 0);
});

test("importing in a browser environment never mounts the page automatically", async () => {
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, "document");
  Object.defineProperty(globalThis, "document", { configurable: true, get() { assert.fail("Import must not inspect document"); } });
  try { await import("../native-conversation.mjs?import-with-browser"); }
  finally { if (descriptor) Object.defineProperty(globalThis, "document", descriptor); else delete globalThis.document; }
});

test("an unconfigured provider stays disabled across pagehide without requesting the microphone", async () => {
  const page = fakeVoiceDocument(); let captures = 0;
  const conversation = mountNativeConversation(page.doc, { lifecycleTarget: page.lifecycle,
    media: { getUserMedia: () => captures++ },
    fetchFn: async () => ({ ok: true, json: async () => ({ providers: [{ id: "grok", configured: false, voices: ["ara"] }] }) }),
  });
  await conversation.mountReady; page.lifecycle.emit("pagehide");
  assert.equal(captures, 0); assert.equal(page.elements.voiceStart.disabled, true);
  assert.equal(page.elements.voiceProvider.disabled, true);
  await conversation.dispose();
});

function selectedPlaceholder(control, label) {
  assert.equal(control.disabled, true);
  assert.equal(control.value, "");
  assert.equal(control.children.length, 1);
  assert.equal(control.children[0].textContent, label);
  assert.equal(control.children[0].value, "");
  assert.equal(control.children[0].selected, true);
}

test("pending provider discovery has labelled disabled choices without requesting audio", async () => {
  const page = fakeVoiceDocument(), discovery = deferred(); let captures = 0;
  const conversation = mountNativeConversation(page.doc, { lifecycleTarget: page.lifecycle,
    media: { getUserMedia: () => captures++ }, fetchFn: () => discovery.promise,
  });
  try {
    selectedPlaceholder(page.elements.voiceProvider, "Checking providers…");
    selectedPlaceholder(page.elements.voiceVoice, "Checking voices…");
    assert.equal(page.elements.voiceStart.disabled, true);
    assert.equal(captures, 0);
  } finally {
    await conversation.dispose();
    discovery.resolve({ ok: false });
    await conversation.mountReady;
  }
});

for (const providers of [[], [{ id: "grok", configured: false, voices: ["ara"] },
  { id: "openai", configured: false, voices: ["marin", "cedar"] }]]) {
  test(`no configured provider leaves labelled unavailable choices (${providers.length} records)`, async () => {
    const page = fakeVoiceDocument(); let captures = 0;
    const conversation = mountNativeConversation(page.doc, { lifecycleTarget: page.lifecycle,
      media: { getUserMedia: () => captures++ },
      fetchFn: async () => ({ ok: true, json: async () => ({ providers }) }),
    });
    try {
      await conversation.mountReady;
      selectedPlaceholder(page.elements.voiceProvider, "No provider configured");
      selectedPlaceholder(page.elements.voiceVoice, "Voice unavailable");
      page.lifecycle.emit("pagehide");
      assert.equal(page.elements.voiceStart.disabled, true);
      assert.equal(captures, 0);
    } finally { await conversation.dispose(); }
  });
}

for (const fetchFn of [async () => ({ ok: false }), async () => { throw new Error("private network detail"); }]) {
  test("discovery failure labels both choices and permits a clean configured remount", async () => {
    const page = fakeVoiceDocument();
    const conversation = mountNativeConversation(page.doc, { lifecycleTarget: page.lifecycle, fetchFn });
    await conversation.mountReady;
    try {
      selectedPlaceholder(page.elements.voiceProvider, "Provider unavailable");
      selectedPlaceholder(page.elements.voiceVoice, "Voice unavailable");
      assert.equal(page.elements.voiceStart.disabled, true);
      assert.equal(page.elements.voiceStatus.textContent, "Native voice broker unavailable");
    } finally { await conversation.dispose(); }
    const recovered = mountNativeConversation(page.doc, { lifecycleTarget: page.lifecycle,
      fetchFn: async () => ({ ok: true, json: async () => ({ providers: [{ id: "grok", configured: true, voices: ["ara", "orion"] }] }) }),
    });
    try {
      await recovered.mountReady;
      assert.equal(page.elements.voiceStart.disabled, false);
      assert.equal(page.elements.voiceProvider.children.length, 1);
      assert.equal(page.elements.voiceProvider.children[0].value, "grok");
      assert.deepEqual(page.elements.voiceVoice.children.map(option => option.value), ["ara", "orion"]);
      assert.equal(page.count(), 7);
    } finally { await recovered.dispose(); }
  });
}

test("only bounded broker drafts reach the host; model output cannot confer authority", async () => {
  const drafts = [], id = "507e8b48-bb41-48b0-a58b-263349b7fdcb";
  let output = { schema: "voice.mission-draft.v1", status: "draft", queued: false, approved: false,
    draft: { id, objective: "Improve the existing voice experience", acceptance: "Verify microphone cleanup and recovery" } };
  const conversation = new NativeConversation({ onMissionDraft: (draft) => drafts.push(draft),
    fetchFn: async () => ({ ok: true, json: async () => output }),
  });
  conversation.active = true; conversation.session = { session_id: "session", capability: "cap" };
  await conversation.executeTool("call", "prepare_mission", {}, conversation.epoch);
  assert.equal(drafts.length, 1); assert.deepEqual(drafts[0], output.draft);
  for (const bad of [ { ...output, queued: true }, { ...output, approved: true },
    { ...output, draft: { ...output.draft, command: "run something" } },
    { ...output, draft: { ...output.draft, objective: "x".repeat(901) } } ]) {
    output = bad; await conversation.executeTool("call", "prepare_mission", {}, conversation.epoch);
  }
  assert.equal(drafts.length, 1);
});

test("stale draft results cannot enter a stopped or replaced conversation", async () => {
  const result = deferred(); let received = 0;
  const conversation = new NativeConversation({ onMissionDraft: () => received++,
    fetchFn: async () => ({ ok: true, json: () => result.promise }),
  });
  conversation.active = true; conversation.session = { session_id: "session", capability: "cap" };
  const request = conversation.executeTool("call", "prepare_mission", {}, conversation.epoch);
  conversation.active = false; conversation.epoch++;
  result.resolve({ schema: "voice.mission-draft.v1", status: "draft", queued: false, approved: false,
    draft: { id: "507e8b48-bb41-48b0-a58b-263349b7fdcb", objective: "Improve the existing voice experience", acceptance: "Verify microphone cleanup and recovery" } });
  await request; assert.equal(received, 0);
});

test("an old tool batch cannot borrow a replacement conversation's session authority", async () => {
  let calls = 0;
  const conversation = new NativeConversation({ fetchFn: () => calls++ });
  conversation.active = true; conversation.epoch = 2;
  const output = await conversation.executeTool("call", "prepare_mission", {}, 1);
  assert.equal(output.status, "unavailable"); assert.equal(calls, 0);
});


test("hidden controls and unpaired surrogates in broker drafts never reach human review", async () => {
  const offered = [], id = "507e8b48-bb41-48b0-a58b-263349b7fdcb";
  let draft = { id, objective: "Improve the existing voice conversation", acceptance: "Verify microphone cleanup and recovery" };
  const conversation = new NativeConversation({ onMissionDraft: value => offered.push(value),
    fetchFn: async () => ({ ok: true, json: async () => ({
      schema: "voice.mission-draft.v1", status: "draft", queued: false, approved: false, draft,
    }) }),
  });
  conversation.active = true; conversation.session = { session_id: "session", capability: "cap" };
  const original = { ...draft };
  for (const field of ["objective", "acceptance"]) {
    for (const bad of ["\x01", "\x1b", "\x7f", "\x85", "\u202e", "\u2067", "\ud800"]) {
      draft = { ...original, [field]: "Improve the existing " + bad + " voice conversation" };
      await conversation.executeTool("call", "prepare_mission", {}, conversation.epoch);
    }
  }
  assert.equal(offered.length, 0);
  for (const text of ["Préparer les voix 🚀 et 👩‍💻\n\tVérifier les interruptions\r\net la reprise",
    "تحسين المحادثة الصوتية والتحقق من الاستعادة"]) {
    draft = { ...original, objective: text, acceptance: text };
    await conversation.executeTool("call", "prepare_mission", {}, conversation.epoch);
    assert.deepEqual(offered.at(-1), draft);
  }
});
