// Keep the person's reviewed words; reject hidden formatting instead of stripping it.
export function isMissionText(text, maxLength = 900) {
  return typeof text === "string" && text.trim().length >= 15 && text.length <= maxLength &&
    !/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f\u202a-\u202e\u2066-\u2069\ud800-\udfff]/u.test(text);
}

const CLEANUP_REQUIRED = "Conversation cleanup is incomplete. Retry cleanup.";

async function waitForSettled(promises, milliseconds) {
  let timer;
  try {
    await Promise.race([Promise.allSettled(promises), new Promise((resolve) => { timer = setTimeout(resolve, milliseconds); })]);
  } finally { clearTimeout(timer); }
}

// Native audio transports. No speechSynthesis, persistent tokens or automatic mic start.
export function pcm16Base64(samples) {
  const bytes = new Uint8Array(samples.length * 2);
  const view = new DataView(bytes.buffer);
  for (let i = 0; i < samples.length; i++) {
    const sample = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(i * 2, Math.round(sample * (sample < 0 ? 32768 : 32767)), true);
  }
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary);
}

export function decodePcm16(encoded) {
  if (typeof encoded !== "string" || encoded.length > 128 * 1024) throw new Error("Audio chunk exceeds the limit");
  const binary = atob(encoded);
  if (!binary.length || binary.length % 2) throw new Error("Invalid PCM audio");
  const samples = new Float32Array(binary.length / 2);
  for (let i = 0; i < samples.length; i++) {
    let value = binary.charCodeAt(i * 2) | (binary.charCodeAt(i * 2 + 1) << 8);
    if (value >= 32768) value -= 65536;
    samples[i] = value / 32768;
  }
  return samples;
}

// Event metadata only. Scheduled PCM is a client estimate; neither transport
// exposes verified per-turn audible playback here. Never substitute zero for unknown.
export class TurnObservations {
  constructor(record, now = () => performance.now()) {
    this.record = record; this.now = now; this.sequence = 0; this.inputEnd = null; this.current = null;
  }
  elapsed(origin = this.inputEnd) { return origin === null ? null : Math.max(0, Math.min(300000, this.now() - origin)); }
  speechStopped() { this.inputEnd = this.now(); }
  created() {
    if (this.current) this.done("unknown");
    if (this.sequence >= 100) throw new Error("Turn limit reached; reconnect to continue");
    this.currentOrigin = this.inputEnd;
    this.current = { sequence: ++this.sequence, status: "unknown", interrupted: false,
      response_created_ms: this.elapsed(), first_scheduled_audio_ms: null, response_done_ms: null, token_counts: null };
  }
  scheduled(delayMs) {
    if (!this.current || this.current.first_scheduled_audio_ms !== null || this.currentOrigin === null || !Number.isFinite(delayMs)) return;
    this.current.first_scheduled_audio_ms = Math.min(300000, this.elapsed(this.currentOrigin) + Math.max(0, delayMs));
  }
  interrupt() { if (this.current) this.current.interrupted = true; }
  done(status, usage) {
    if (!this.current) return;
    const observation = this.current; this.current = null;
    observation.status = ["completed", "cancelled", "failed", "incomplete", "stopped"].includes(status) ? status : "unknown";
    observation.response_done_ms = this.elapsed(this.currentOrigin);
    if (usage && [usage.input_tokens, usage.output_tokens].every((value) => Number.isInteger(value) && value >= 0 && value <= 1e7)) {
      observation.token_counts = { input_tokens: usage.input_tokens, output_tokens: usage.output_tokens };
    }
    this.record(observation);
  }
}

// Wait for the complete response and all parallel tool results before ONE
// response.create. Never race individual tool completions against generation.
export class ToolBatch {
  constructor(execute, send) { this.execute = execute; this.send = send; this.pending = []; this.seen = new Set(); }
  add(event) {
    if (typeof event.call_id !== "string" || this.seen.has(event.call_id)) return;
    if (this.seen.size >= 20) throw new Error("Session tool limit reached");
    this.seen.add(event.call_id);
    const promise = Promise.resolve().then(() => {
      if (typeof event.arguments !== "string" || event.arguments.length > 2048) throw new Error("Invalid tool arguments");
      return this.execute(event.call_id, event.name, JSON.parse(event.arguments));
    }).catch(() => ({ status: "unavailable", reason: "Scoped tool call failed" })).then((output) => {
      this.send({ type: "conversation.item.create", item: {
        type: "function_call_output", call_id: event.call_id, output: JSON.stringify(output),
      } });
    });
    this.pending.push(promise);
  }
  async done(resume = true) {
    const pending = this.pending.splice(0);
    if (!pending.length) return;
    await Promise.all(pending);
    if (typeof resume === "function" ? resume() : resume) this.send({ type: "response.create" });
  }
}

export class PcmPlayback {
  constructor(context, rate = 48000) {
    this.context = context; this.rate = rate; this.nodes = new Set();
    this.nextTime = 0; this.itemId = null;
    this.contents = new Map(); this.currentContent = null; this.lastOutputTime = null;
  }
  outputPosition() {
    // currentTime describes graph processing, not the device's output frame.
    // Never extrapolate a missing clock into speech the person may not have heard.
    try {
      const timestamp = this.context.getOutputTimestamp();
      const position = timestamp.contextTime;
      if (!Number.isFinite(position) || position < 0 || !Number.isFinite(this.context.currentTime) || position > this.context.currentTime ||
          !Number.isFinite(timestamp.performanceTime) || timestamp.performanceTime < 0 ||
          (this.lastOutputTime !== null && position < this.lastOutputTime)) return null;
      this.lastOutputTime = position;
      return position;
    } catch { return null; }
  }
  advance(position) {
    if (position === null) return;
    for (const content of this.contents.values()) {
      while (content.segments.length && content.segments[0].end <= position + 1e-9) {
        content.playedFrames += content.segments.shift().frames;
      }
    }
  }
  playedFrames(content, position) {
    let frames = content.playedFrames;
    for (const segment of content.segments) {
      frames += Math.min(segment.frames, Math.max(0, Math.floor((position - segment.start) * this.rate + 1e-7)));
    }
    return Math.min(content.totalFrames, frames);
  }
  enqueue(encoded, itemId, contentIndex = 0) {
    if (typeof itemId !== "string" || !itemId.length || itemId.length > 128 ||
        !Number.isInteger(contentIndex) || contentIndex < 0 || contentIndex > 15) {
      throw new Error("Invalid audio content identity");
    }
    this.advance(this.outputPosition());
    const key = JSON.stringify([itemId, contentIndex]);
    if (this.nodes.size >= 256 || [...this.contents.values()].reduce((count, content) => count + content.segments.length, 0) >= 256 ||
        (!this.contents.has(key) && this.contents.size >= 128)) {
      throw new Error("Audio playback buffer limit reached; reconnect to recover");
    }
    const samples = decodePcm16(encoded);
    if (this.nextTime - this.context.currentTime + samples.length / this.rate > 3) {
      throw new Error("Audio playback fell behind; reconnect to recover");
    }
    const buffer = this.context.createBuffer(1, samples.length, this.rate);
    buffer.copyToChannel(samples, 0);
    const source = this.context.createBufferSource();
    // Own the node before connect/start so a failure remains available to Stop.
    this.nodes.add(source);
    source.buffer = buffer;
    source.connect(this.context.destination);
    this.itemId = itemId;
    const start = Math.max(this.context.currentTime + 0.015, this.nextTime);
    this.nextTime = start + buffer.duration;
    source.onended = () => {
      try { source.disconnect(); this.nodes.delete(source); }
      catch { /* Keep the completed node owned for Stop's cleanup retry. */ }
    };
    source.start(start);
    const content = this.contents.get(key) || { itemId, contentIndex, totalFrames: 0, playedFrames: 0, segments: [] };
    content.segments.push({ start, end: this.nextTime, frames: samples.length });
    content.totalFrames += samples.length;
    this.contents.set(key, content); this.currentContent = key;
    return Math.max(0, (start - this.context.currentTime) * 1000);
  }
  interrupt() {
    const position = this.outputPosition(), truncations = [];
    const timingUnavailable = this.contents.size > 0 && position === null;
    let heardMs = this.contents.size ? null : 0;
    if (!timingUnavailable) {
      this.advance(position);
      for (const [key, content] of this.contents) {
        const frames = this.playedFrames(content, position);
        const audioEndMs = Math.floor(frames * 1000 / this.rate);
        if (key === this.currentContent) heardMs = audioEndMs;
        if (frames < content.totalFrames || key === this.currentContent) {
          truncations.push({ itemId: content.itemId, contentIndex: content.contentIndex, audioEndMs });
        }
      }
    }
    const itemId = this.itemId;
    for (const node of this.nodes) {
      node.onended = null;
      try { node.stop(); } catch { /* already ended */ }
      try { node.disconnect(); this.nodes.delete(node); } catch { /* retained for cleanup retry */ }
    }
    this.nextTime = 0; this.itemId = null;
    this.contents.clear(); this.currentContent = null;
    if (this.nodes.size) throw new Error(CLEANUP_REQUIRED);
    return { itemId, heardMs, truncations, timingUnavailable };
  }
}

export class NativeConversation {
  constructor({ onState = () => {}, onText = () => {}, onMissionDraft = () => {}, fetchFn = globalThis.fetch.bind(globalThis), media = globalThis.navigator?.mediaDevices,
    AudioContextClass = globalThis.AudioContext, WebSocketClass = globalThis.WebSocket, PeerClass = globalThis.RTCPeerConnection,
    WorkletClass = globalThis.AudioWorkletNode, audioFactory = () => new Audio(), brokerPrefix = "/voice",
    workletPath = "/dashboard/pcm-capture.js" } = {}) {
    // Capabilities and worklets must never be sent to configurable remote origins.
    if (typeof brokerPrefix !== "string" || brokerPrefix.length > 200 || !/^(?:\/[A-Za-z0-9_-]+)+$/.test(brokerPrefix)) {
      throw new Error("Voice broker prefix must be a same-origin path");
    }
    if (typeof workletPath !== "string" || workletPath.length > 200 || !/^(?:\/[A-Za-z0-9_-]+)+\.js$/.test(workletPath)) {
      throw new Error("Voice worklet must be a same-origin JavaScript path");
    }
    Object.assign(this, { onState, onText, onMissionDraft, fetchFn, media, AudioContextClass, WebSocketClass, PeerClass, WorkletClass, audioFactory, brokerPrefix, workletPath });
    this.epoch = 0; this.responseGeneration = 0; this.active = false; this.ready = false; this.muted = false;
    this.stopping = false; this.cleanupTasks = new Set(); this.stopPromise = null;
  }
  get cleanupFailed() { return this.cleanupTasks.size > 0; }
  state(label, error = false) {
    this.onState({ label, error, active: this.active, ready: this.ready, muted: this.muted,
      stopping: this.stopping, cleanupFailed: this.cleanupFailed });
  }
  endpoint(action) {
    if (!["session", "tool", "observation", "stop", "capabilities", "component-offer"].includes(action)) throw new Error("Unknown voice broker action");
    return `${this.brokerPrefix}/${action}`;
  }
  async post(path, body, { signal = this.abort?.signal, keepalive = false, capability = this.session?.capability } = {}) {
    if (!path.startsWith("/voice/")) throw new Error("Unknown voice broker action");
    const response = await this.fetchFn(this.endpoint(path.slice(7)), {
      method: "POST", headers: { "Content-Type": "application/json", ...(capability ? { "X-Voice-Capability": capability } : {}) },
      body: JSON.stringify(body), signal, keepalive, cache: "no-store", credentials: "same-origin", redirect: "error",
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Local voice broker unavailable");
    return result;
  }
  async executeTool(callId, name, args, epoch) {
    if (epoch !== this.epoch || !this.active) return { status: "unavailable", reason: "Voice conversation ended" };
    const output = await this.post("/voice/tool", { session_id: this.session?.session_id, call_id: callId, name, arguments: args });
    const draft = output?.draft;
    // Display only the fixed broker result, never a raw model event or authority claim.
    if (epoch === this.epoch && this.active && name === "prepare_mission" &&
      output?.schema === "voice.mission-draft.v1" && output.status === "draft" && output.queued === false && output.approved === false &&
      draft && Object.keys(draft).sort().join() === "acceptance,id,objective" &&
      typeof draft.id === "string" && /^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/.test(draft.id) &&
      [draft.objective, draft.acceptance].every((text) => isMissionText(text))) {
      this.onMissionDraft(Object.freeze({ id: draft.id, objective: draft.objective, acceptance: draft.acceptance }));
    }
    return output;
  }
  send(event) {
    if (!this.active || !this.channel || this.channel.readyState !== (this.session?.provider === "grok" ? 1 : "open")) return;
    if (this.channel.bufferedAmount > 1024 * 1024) throw new Error("Audio upload fell behind; reconnect to recover");
    this.channel.send(JSON.stringify(event));
  }
  async start(provider, voice) {
    if (this.active || this.stopping) return;
    if (this.cleanupFailed) { this.state(CLEANUP_REQUIRED, true); return; }
    const epoch = ++this.epoch;
    this.active = true; this.ready = false; this.muted = false; this.blockAudio = false; this.responding = false; this.interrupted = false;
    this.providerResponseId = null;
    this.abort = new AbortController();
    this.state("Requesting microphone permission…");
    try {
      if (!this.media?.getUserMedia) throw new Error("Microphone access requires a supported browser on localhost");
      if (provider === "grok") {
        this.context = new this.AudioContextClass({ sampleRate: 48000 });
        await this.context.resume();
      }
      if (epoch !== this.epoch) return;
      const stream = await this.media.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true }, video: false });
      if (epoch !== this.epoch) { stream.getTracks().forEach((track) => track.stop()); return; }
      this.stream = stream;
      stream.getAudioTracks().forEach((track) => { track.enabled = false; track.onended = () => this.stop("Microphone disconnected"); });
      this.state("Connecting to the voice provider…");
      const session = await this.post("/voice/session", { provider, voice });
      if (epoch !== this.epoch) {
        if (session.provider === "elevenlabs") {
          this.rememberCleanup(() => this.releaseSession(session));
          await waitForSettled(this.retryCleanup(), 1000);
          if (this.cleanupFailed && !this.active && !this.stopping) this.state(CLEANUP_REQUIRED, true);
        } else await this.releaseSession(session);
        return;
      }
      this.session = session;
      this.observationWrites = new Set();
      this.observationAbort = new AbortController();
      const writes = this.observationWrites, observationAbort = this.observationAbort;
      this.observations = provider === "elevenlabs" ? null : new TurnObservations((observation) => {
        if (writes.size >= 3) {
          if (epoch === this.epoch) this.state("Listening · turn history fell behind", true);
          return;
        }
        const request = this.post("/voice/observation", { session_id: session.session_id, observation }, {
          capability: session.capability,
          signal: AbortSignal.any([observationAbort.signal, AbortSignal.timeout(2000)]), keepalive: true,
        }).catch(() => { if (epoch === this.epoch) this.state("Listening · turn history unavailable", true); });
        writes.add(request);
        request.finally(() => writes.delete(request));
      });
      this.tools = new ToolBatch((callId, name, args) => this.executeTool(callId, name, args, epoch),
        (event) => { if (epoch === this.epoch) this.send(event); });
      this.limitTimer = setTimeout(() => this.stop("Session time limit reached"), session.max_session_seconds * 1000);
      this.readyTimer = setTimeout(() => this.stop("Connection timed out; check provider access", true), provider === "elevenlabs" ? 25000 : 15000);
      if (provider === "grok") await this.connectGrok(epoch);
      else if (provider === "elevenlabs") await this.connectComponent(epoch);
      else await this.connectOpenAI(epoch);
    } catch (error) {
      if (epoch === this.epoch) await this.stop(error.name === "NotAllowedError" ? "Microphone permission was denied. Allow it, then start again." : error.message, true);
    }
  }
  listen() {
    if (!this.active) return;
    clearTimeout(this.readyTimer);
    this.ready = true;
    this.stream?.getAudioTracks().forEach((track) => { track.enabled = !this.muted; });
    this.state(this.muted ? "Microphone muted" : "Listening");
  }
  async connectGrok(epoch) {
    const context = this.context;
    if (context.sampleRate !== 48000) throw new Error("This browser did not provide the required 48 kHz audio context");
    await context.audioWorklet.addModule(this.workletPath);
    if (epoch !== this.epoch) return;
    this.playback = new PcmPlayback(context);
    this.source = context.createMediaStreamSource(this.stream);
    this.capture = new this.WorkletClass(context, "starlight-pcm-capture");
    this.silent = context.createGain(); this.silent.gain.value = 0;
    this.source.connect(this.capture); this.capture.connect(this.silent); this.silent.connect(context.destination);
    const session = this.session;
    const ws = new this.WebSocketClass(`wss://api.x.ai/v1/realtime?model=${encodeURIComponent(session.model)}`, [`xai-client-secret.${session.client_secret.value}`]);
    this.channel = ws;
    // Erase the short-lived value from retained session state after transport setup.
    delete session.client_secret;
    ws.onopen = () => {
      if (epoch !== this.epoch) return;
      this.send({ type: "session.update", session: {
        voice: session.voice, instructions: session.instructions, tools: session.tools,
        reasoning: { effort: "none" }, turn_detection: { type: "server_vad" },
        audio: { input: { format: { type: "audio/pcm", rate: 48000 } }, output: { format: { type: "audio/pcm", rate: 48000 } } },
      } });
    };
    this.capture.port.onmessage = ({ data }) => {
      if (epoch !== this.epoch || !this.ready || this.muted) return;
      try { this.send({ type: "input_audio_buffer.append", audio: pcm16Base64(data) }); }
      catch (error) { this.stop(error.message, true); }
    };
    this.bindEvents(ws, epoch);
  }
  async connectOpenAI(epoch) {
    const pc = new this.PeerClass(); this.peer = pc;
    const audio = this.audioFactory(); this.audio = audio;
    audio.autoplay = true;
    pc.ontrack = ({ streams }) => {
      if (epoch !== this.epoch) return;
      audio.srcObject = streams[0];
      audio.play().catch(() => { if (epoch === this.epoch) this.stop("Audio playback was blocked. Allow audio, then reconnect.", true); });
    };
    pc.onconnectionstatechange = () => {
      if (epoch === this.epoch && ["failed", "disconnected", "closed"].includes(pc.connectionState)) this.stop("Voice connection ended");
    };
    this.stream.getTracks().forEach((track) => pc.addTrack(track, this.stream));
    const dc = pc.createDataChannel("oai-events"); this.channel = dc;
    dc.onopen = () => { if (epoch === this.epoch) this.listen(); };
    this.bindEvents(dc, epoch);
    const offer = await pc.createOffer();
    if (epoch !== this.epoch) return;
    await pc.setLocalDescription(offer);
    const response = await this.fetchFn("https://api.openai.com/v1/realtime/calls", {
      method: "POST", headers: { Authorization: `Bearer ${this.session.client_secret.value}`, "Content-Type": "application/sdp" },
      body: offer.sdp, signal: this.abort.signal,
    });
    delete this.session?.client_secret;
    if (!response.ok) throw new Error("OpenAI WebRTC negotiation failed; check model access");
    const answer = await response.text();
    if (epoch !== this.epoch) return;
    if (answer.length > 64 * 1024) throw new Error("Provider SDP exceeds the limit");
    await pc.setRemoteDescription({ type: "answer", sdp: answer });
  }
  async connectComponent(epoch) {
    const pc = new this.PeerClass({ iceServers: [] }); this.peer = pc;
    const audio = this.audioFactory(); this.audio = audio; audio.autoplay = true;
    pc.ontrack = ({ streams }) => {
      if (epoch !== this.epoch) return;
      audio.srcObject = streams[0];
      audio.play().catch(() => {
        if (epoch === this.epoch) this.stop("Audio playback was blocked. Allow audio, then reconnect.", true);
      });
    };
    pc.onconnectionstatechange = () => {
      if (epoch === this.epoch && ["failed", "disconnected", "closed"].includes(pc.connectionState)) this.stop("Component voice connection ended");
    };
    this.stream.getTracks().forEach((track) => pc.addTrack(track, this.stream));
    const dc = pc.createDataChannel("pipecat"); this.channel = dc;
    dc.onopen = () => {
      if (epoch !== this.epoch) return;
      this.listen();
      clearInterval(this.componentHeartbeat);
      const heartbeat = () => {
        if (epoch !== this.epoch || dc.readyState !== "open") return;
        try { dc.send("ping"); }
        catch { this.stop("Component voice transport failed", true); }
      };
      this.componentHeartbeat = setInterval(heartbeat, 5000);
      heartbeat();
    };
    dc.onmessage = ({ data }) => {
      if (epoch !== this.epoch) return;
      try {
        if (typeof data !== "string" || data.length > 8192) throw new Error("Component caption exceeds the limit");
        const message = JSON.parse(data);
        if (message.type !== "component.caption") return;
        if (message.reset === true) this.onText("", true);
        if (typeof message.text === "string" && message.text.length <= 4096) this.onText(message.text);
      } catch (error) { this.stop(error.message, true); }
    };
    dc.onerror = () => { if (epoch === this.epoch) this.stop("Component voice transport failed", true); };
    dc.onclose = () => { if (epoch === this.epoch) this.stop("Component voice connection ended"); };
    const offer = await pc.createOffer();
    if (epoch !== this.epoch) return;
    await pc.setLocalDescription(offer);
    if (pc.iceGatheringState !== "complete") {
      await new Promise((resolve, reject) => {
        const signal = this.abort.signal;
        const cleanup = () => {
          clearTimeout(timer); pc.removeEventListener("icegatheringstatechange", changed);
          signal.removeEventListener("abort", aborted);
        };
        const changed = () => { if (pc.iceGatheringState === "complete") { cleanup(); resolve(); } };
        const aborted = () => { cleanup(); reject(new Error("Component connection stopped")); };
        const timer = setTimeout(() => { cleanup(); reject(new Error("Component media negotiation timed out")); }, 8000);
        pc.addEventListener("icegatheringstatechange", changed); signal.addEventListener("abort", aborted, { once: true });
        if (signal.aborted) aborted(); else changed();
      });
    }
    if (epoch !== this.epoch) return;
    const answer = await this.post("/voice/component-offer", {
      session_id: this.session.session_id, sdp: pc.localDescription.sdp, type: "offer",
    });
    if (epoch !== this.epoch) return;
    if (answer.type !== "answer" || typeof answer.sdp !== "string" || answer.sdp.length > 48 * 1024) throw new Error("Component SDP answer is invalid");
    await pc.setRemoteDescription({ type: "answer", sdp: answer.sdp });
  }
  bindEvents(channel, epoch) {
    channel.onmessage = ({ data }) => {
      if (epoch !== this.epoch) return;
      try {
        if (typeof data !== "string" || data.length > 256 * 1024) throw new Error("Provider event exceeds the limit");
        this.event(JSON.parse(data));
      } catch (error) { this.stop(error.message, true); }
    };
    channel.onerror = () => { if (epoch === this.epoch) this.stop("Voice transport failed; check account access and network", true); };
    channel.onclose = () => { if (epoch === this.epoch) this.stop("Voice connection closed"); };
  }
  event(event) {
    const responseId = event.response_id ?? event.response?.id;
    if (event.type !== "response.created" && typeof responseId === "string" && this.providerResponseId && responseId !== this.providerResponseId) return;
    switch (event.type) {
      case "session.updated": this.listen(); break;
      case "response.created":
        if (typeof responseId === "string" && responseId === this.providerResponseId) return;
        ++this.responseGeneration;
        this.providerResponseId = typeof responseId === "string" && responseId.length <= 128 ? responseId : null;
        this.observations?.created(); this.blockAudio = false; this.responding = true; this.interrupted = false;
        this.onText("\n"); this.state("Responding"); break;
      case "input_audio_buffer.speech_stopped": this.observations?.speechStopped(); break;
      case "input_audio_buffer.speech_started": {
        this.interrupted = true;
        this.observations?.interrupt();
        if (this.session?.provider === "grok") {
          this.blockAudio = true;
          // The configured server VAD cancels generation itself. A second
          // manual cancellation can arrive after the response is already gone.
          const { truncations, timingUnavailable } = this.playback?.interrupt() || {};
          if (timingUnavailable) {
            this.stop("Voice playback could not be synchronized. Reconnect to continue.", true);
            return;
          }
          for (const content of truncations || []) this.send({ type: "conversation.item.truncate",
            item_id: content.itemId, content_index: content.contentIndex, audio_end_ms: content.audioEndMs });
        }
        this.state("Listening"); break;
      }
      case "response.output_audio.delta": case "response.audio.delta":
        if (!this.blockAudio && this.playback) {
          const delayMs = this.playback.enqueue(event.delta, event.item_id, event.content_index ?? 0);
          this.observations?.scheduled(delayMs);
        }
        break;
      case "response.output_audio_transcript.delta": case "response.audio_transcript.delta":
      case "response.output_text.delta": case "response.text.delta":
        this.onText(String(event.delta ?? "").slice(0, 2000)); break;
      case "response.function_call_arguments.done": this.tools?.add(event); break;
      case "response.done": {
        const epoch = this.epoch, generation = this.responseGeneration;
        const resumable = !this.interrupted && event.response?.status !== "cancelled" && event.response?.status !== "failed";
        this.responding = false;
        this.observations?.done(event.response?.status, event.response?.usage);
        this.tools?.done(() => resumable && this.active && this.epoch === epoch &&
          this.responseGeneration === generation && !this.interrupted)
          .catch(() => this.stop("Tool result could not be delivered", true));
        this.state(this.muted ? "Microphone muted" : "Listening"); break;
      }
      case "error": throw new Error("Voice provider rejected the session or event; check server configuration");
    }
  }
  mute() {
    if (!this.ready) return;
    this.muted = !this.muted;
    this.stream?.getAudioTracks().forEach((track) => { track.enabled = !this.muted; });
    if (this.session?.provider === "grok") this.send({ type: "input_audio_buffer.clear" });
    this.state(this.muted ? "Microphone muted" : "Listening");
  }
  async releaseSession(session) {
    if (!session) return;
    try {
      await this.post("/voice/stop", { session_id: session.session_id }, {
        signal: AbortSignal.timeout(3000), keepalive: true, capability: session.capability,
      });
    } catch (error) {
      if (session.provider === "elevenlabs") throw error;
      /* Native authority expires even if the cleanup request cannot arrive. */
    }
  }
  rememberCleanup(run) { this.cleanupTasks.add({ run, pending: null }); }
  retryCleanup() {
    const pending = [];
    for (const task of this.cleanupTasks) {
      if (!task.pending) {
        try {
          const result = task.run();
          if (result && typeof result.then === "function") {
            task.pending = Promise.resolve(result).then(() => {
              this.cleanupTasks.delete(task);
              if (!this.stopping && !this.active && !this.cleanupFailed) {
                try { this.state("Stopped"); } catch { /* An unavailable view cannot retain audio resources. */ }
              }
            }, () => { task.pending = null; });
          } else this.cleanupTasks.delete(task);
        } catch { /* Retain only this failed action; other resources still close. */ }
      }
      if (task.pending) pending.push(task.pending);
    }
    return pending;
  }
  stop(label = "Stopped", error = false) {
    if (this.stopPromise) return this.stopPromise;
    this.stopping = true;
    ++this.epoch; this.active = false; this.ready = false;
    clearTimeout(this.readyTimer); clearTimeout(this.limitTimer); this.abort?.abort();
    clearInterval(this.componentHeartbeat); this.componentHeartbeat = null;
    const { stream, channel, peer, audio, playback, source, capture, silent, context, session, observations } = this;
    this.stream = this.channel = this.peer = this.audio = this.playback = this.context = this.session = this.observations = null;
    this.source = this.capture = this.silent = null;
    this.tools = null;
    const writeSet = this.observationWrites;
    const writes = [...(writeSet || [])];
    const observationAbort = this.observationAbort;
    this.observationWrites = this.observationAbort = null;
    this.stopPromise = Promise.resolve().then(async () => {
      try { this.state("Stopping…"); } catch { /* Cleanup must survive a detached or failing view. */ }
      try { observations?.done("stopped"); } catch { /* History cannot prevent media revocation. */ }
      // Include the final observation created above, without keeping its request set.
      for (const write of writeSet || []) if (!writes.includes(write)) writes.push(write);
      if (stream) this.rememberCleanup(() => {
        let failed = false;
        for (const track of stream.getTracks()) {
          track.onended = null; track.enabled = false;
          try { track.stop(); } catch { failed = true; }
        }
        if (failed) throw new Error(CLEANUP_REQUIRED);
      });
      if (channel) this.rememberCleanup(() => {
        channel.onopen = channel.onclose = channel.onerror = channel.onmessage = null;
        channel.close();
      });
      if (peer) this.rememberCleanup(() => { peer.ontrack = peer.onconnectionstatechange = null; peer.close(); });
      if (audio) this.rememberCleanup(() => { try { audio.pause(); } finally { audio.srcObject = null; } });
      if (playback) this.rememberCleanup(() => playback.interrupt());
      for (const node of [source, capture, silent]) if (node) this.rememberCleanup(() => {
        if (node === capture) node.port.onmessage = null;
        node.disconnect();
      });
      if (context) this.rememberCleanup(() => context.close());
      if (session?.provider === "elevenlabs") this.rememberCleanup(() => this.releaseSession(session));
      try {
        await waitForSettled(this.retryCleanup(), 1000);
        await waitForSettled(writes, 2000);
      } finally {
        observationAbort?.abort();
        if (session?.provider !== "elevenlabs") await this.releaseSession(session);
        this.stopping = false; this.stopPromise = null;
        try { this.state(this.cleanupFailed ? CLEANUP_REQUIRED : label, this.cleanupFailed || error); }
        catch { /* The caller may have disposed its view while release was pending. */ }
      }
    });
    return this.stopPromise;
  }
}

// Mount explicitly. Importing this module never touches the page or microphone.
// The returned conversation can be disposed immediately, even during discovery.
export function mountNativeConversation(doc = document, options = {}) {
  const start = doc.getElementById("voiceStart"), stop = doc.getElementById("voiceStop"), mute = doc.getElementById("voiceMute");
  const provider = doc.getElementById("voiceProvider"), voice = doc.getElementById("voiceVoice");
  const status = doc.getElementById("voiceStatus"), caption = doc.getElementById("voiceCaption");
  const mission = doc.getElementById("voiceMissionDraft");
  if (![start, stop, mute, provider, voice, status, caption].every(Boolean)) throw new Error("Native voice controls are missing");
  let disposed = false, configured = false;
  const discovery = new AbortController(), listeners = [];
  const bind = (target, name, handler) => {
    target.addEventListener(name, handler); listeners.push(() => target.removeEventListener(name, handler));
  };
  const placeholder = (control, label) => {
    const option = doc.createElement("option");
    option.value = ""; option.textContent = label; option.disabled = true; option.selected = true;
    control.replaceChildren(); control.append(option); control.value = "";
  };
  const conversation = new NativeConversation({ ...options,
    onState: (state) => {
      if (disposed) return;
      status.textContent = state.label; status.classList.toggle("err", state.error);
      const blocked = state.active || state.stopping || state.cleanupFailed;
      start.disabled = !configured || blocked;
      stop.disabled = state.stopping || (!state.active && !state.cleanupFailed); mute.disabled = !state.ready;
      provider.disabled = voice.disabled = !configured || blocked;
      stop.textContent = state.cleanupFailed && !state.stopping ? "Retry cleanup" : "Stop";
      mute.textContent = state.muted ? "Unmute" : "Mute"; mute.setAttribute("aria-pressed", String(state.muted));
    },
    onText: (text, reset = false) => { if (!disposed) caption.textContent = ((reset ? "" : caption.textContent) + text).slice(-2000); },
    onMissionDraft: (draft) => {
      if (disposed) return;
      if (options.onMissionDraft) options.onMissionDraft(draft);
      else if (mission) { mission.hidden = false; mission.textContent = `Unsaved mission draft\n${draft.objective}\n\nAcceptance\n${draft.acceptance}\n\nReview product, role, source and budget in Command Center before saving.`; }
    },
  });
  start.disabled = stop.disabled = mute.disabled = provider.disabled = voice.disabled = true;
  placeholder(provider, "Checking providers…"); placeholder(voice, "Checking voices…");
  bind(start, "click", () => { caption.textContent = ""; conversation.start(provider.value, voice.value); });
  bind(stop, "click", () => conversation.stop());
  bind(mute, "click", () => conversation.mute());
  bind(doc, "keydown", (event) => { if (event.key === "Escape" && conversation.active) conversation.stop(); });
  bind(doc, "visibilitychange", () => { if (doc.hidden && conversation.active) conversation.stop("Stopped when the tab became hidden"); });
  bind(options.lifecycleTarget || globalThis, "pagehide", () => { if (conversation.active) conversation.stop(); });
  conversation.dispose = async () => {
    if (disposed) return;
    disposed = true; discovery.abort(); listeners.splice(0).forEach((remove) => remove());
    start.disabled = stop.disabled = mute.disabled = provider.disabled = voice.disabled = true;
    provider.replaceChildren(); voice.replaceChildren();
    caption.textContent = "";
    if (mission) { mission.textContent = ""; mission.hidden = true; }
    await conversation.stop();
  };
  conversation.mountReady = (async () => { try {
    const response = await conversation.fetchFn(conversation.endpoint("capabilities"), {
      cache: "no-store", credentials: "same-origin", redirect: "error", signal: AbortSignal.any([discovery.signal, AbortSignal.timeout(5000)]),
    });
    if (!response.ok) throw new Error("Voice broker unavailable");
    const caps = await response.json();
    if (disposed) return;
    provider.replaceChildren();
    let choices = [];
    for (const spec of caps.providers) {
      const option = doc.createElement("option"); option.value = spec.id;
      const label = spec.id === "grok" ? "Grok native voice" : spec.id === "elevenlabs" ? "ElevenLabs component" : "OpenAI WebRTC";
      option.textContent = label + (spec.configured ? "" : spec.id === "elevenlabs" ? " · setup needed" : " · key needed");
      option.disabled = !spec.configured; provider.append(option);
      if (spec.configured) choices.push(spec);
    }
    const chooseVoice = () => {
      voice.replaceChildren();
      for (const name of caps.providers.find((spec) => spec.id === provider.value)?.voices || []) {
        const option = doc.createElement("option"); option.value = name;
        option.textContent = provider.value === "elevenlabs" ? "Configured voice" : name[0].toUpperCase() + name.slice(1); voice.append(option);
      }
    };
    bind(provider, "change", chooseVoice);
    if (choices.length) {
      configured = true; provider.value = choices[0].id; chooseVoice();
      if (!conversation.active && !conversation.stopping && !conversation.cleanupFailed) conversation.state("Ready · microphone off");
      else if (conversation.stopping) conversation.state("Stopping…");
      else if (conversation.cleanupFailed) conversation.state(CLEANUP_REQUIRED, true);
    }
    else {
      placeholder(provider, "No provider configured"); placeholder(voice, "Voice unavailable");
      status.textContent = "Configure XAI_API_KEY or OPENAI_API_KEY on the server to enable native voice";
      if (caps.providers.some((spec) => spec.id === "elevenlabs")) status.textContent += ", or configure the ElevenLabs component runtime and account voice";
    }
  } catch { if (!disposed) {
    configured = false;
    placeholder(provider, "Provider unavailable"); placeholder(voice, "Voice unavailable");
    conversation.state("Native voice broker unavailable", true);
  } } })();
  return conversation;
}
