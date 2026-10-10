import test from "node:test";
import assert from "node:assert/strict";
import { NativeConversation, PcmPlayback, ToolBatch, TurnObservations, decodePcm16, pcm16Base64 } from "../native-conversation.mjs";

const flush = () => new Promise((resolve) => setImmediate(resolve));
const deferred = () => { let resolve; const promise = new Promise((r) => { resolve = r; }); return { promise, resolve }; };

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
    constructor() { Object.assign(this, fakeContext()); this.sampleRate = 48000; contexts.push(this); this.audioWorklet = { addModule: async () => {} }; }
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
    AudioContextClass: Context, WebSocketClass: Socket, WorkletClass: Worklet,
    fetchFn: async (path, options) => {
      requests.push({ path, options });
      return { ok: true, json: async () => path === "/voice/session" ? {
        provider: "grok", voice: "ara", model: "grok-voice-think-fast-2.0", session_id: "local-id", capability: "cap",
        client_secret: { value: "ephemeral-token" }, instructions: "server-owned", tools: [], max_session_seconds: 300,
      } : { ok: true } };
    },
  });
  await conversation.start("grok", "ara");
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
