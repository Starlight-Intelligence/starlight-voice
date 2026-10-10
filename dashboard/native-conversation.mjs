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
    if (resume) this.send({ type: "response.create" });
  }
}

export class PcmPlayback {
  constructor(context, rate = 48000) {
    this.context = context; this.rate = rate; this.nodes = new Set();
    this.nextTime = 0; this.firstStart = null; this.itemId = null;
  }
  enqueue(encoded, itemId) {
    const samples = decodePcm16(encoded);
    if (this.nextTime - this.context.currentTime + samples.length / this.rate > 3) {
      throw new Error("Audio playback fell behind; reconnect to recover");
    }
    const buffer = this.context.createBuffer(1, samples.length, this.rate);
    buffer.copyToChannel(samples, 0);
    const source = this.context.createBufferSource();
    source.buffer = buffer;
    source.connect(this.context.destination);
    if (itemId && itemId !== this.itemId) {
      this.itemId = itemId; this.firstStart = null;
    }
    const start = Math.max(this.context.currentTime + 0.015, this.nextTime);
    this.firstStart ??= start;
    this.nextTime = start + buffer.duration;
    this.nodes.add(source);
    source.onended = () => { this.nodes.delete(source); source.disconnect(); };
    source.start(start);
  }
  interrupt() {
    const heardMs = this.firstStart === null ? 0 : Math.max(0, Math.floor((Math.min(this.context.currentTime, this.nextTime) - this.firstStart) * 1000));
    const itemId = this.itemId;
    for (const node of this.nodes) { node.onended = null; try { node.stop(); } catch { /* already ended */ } node.disconnect(); }
    this.nodes.clear(); this.nextTime = 0; this.firstStart = null; this.itemId = null;
    return { itemId, heardMs };
  }
}

export class NativeConversation {
  constructor({ onState = () => {}, onText = () => {}, fetchFn = globalThis.fetch, media = globalThis.navigator?.mediaDevices,
    AudioContextClass = globalThis.AudioContext, WebSocketClass = globalThis.WebSocket, PeerClass = globalThis.RTCPeerConnection,
    WorkletClass = globalThis.AudioWorkletNode, audioFactory = () => new Audio() } = {}) {
    Object.assign(this, { onState, onText, fetchFn, media, AudioContextClass, WebSocketClass, PeerClass, WorkletClass, audioFactory });
    this.epoch = 0; this.active = false; this.ready = false; this.muted = false;
  }
  state(label, error = false) { this.onState({ label, error, active: this.active, ready: this.ready, muted: this.muted }); }
  async post(path, body, { signal = this.abort?.signal, keepalive = false, capability = this.session?.capability } = {}) {
    const response = await this.fetchFn(path, {
      method: "POST", headers: { "Content-Type": "application/json", ...(capability ? { "X-Voice-Capability": capability } : {}) },
      body: JSON.stringify(body), signal, keepalive, cache: "no-store", credentials: "same-origin",
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Local voice broker unavailable");
    return result;
  }
  send(event) {
    if (!this.active || !this.channel || this.channel.readyState !== (this.session?.provider === "grok" ? 1 : "open")) return;
    if (this.channel.bufferedAmount > 1024 * 1024) throw new Error("Audio upload fell behind; reconnect to recover");
    this.channel.send(JSON.stringify(event));
  }
  async start(provider, voice) {
    if (this.active) return;
    const epoch = ++this.epoch;
    this.active = true; this.ready = false; this.muted = false; this.blockAudio = false; this.responding = false; this.interrupted = false;
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
      if (epoch !== this.epoch) { await this.releaseSession(session); return; }
      this.session = session;
      this.tools = new ToolBatch(async (callId, name, args) => this.post("/voice/tool", {
        session_id: session.session_id, call_id: callId, name, arguments: args,
      }), (event) => { if (epoch === this.epoch) this.send(event); });
      this.limitTimer = setTimeout(() => this.stop("Session time limit reached"), session.max_session_seconds * 1000);
      this.readyTimer = setTimeout(() => this.stop("Connection timed out; check provider access", true), 15000);
      if (provider === "grok") await this.connectGrok(epoch);
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
    await context.audioWorklet.addModule("/dashboard/pcm-capture.js");
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
      audio.play().catch(() => this.stop("Audio playback was blocked. Allow audio, then reconnect.", true));
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
    switch (event.type) {
      case "session.updated": this.listen(); break;
      case "response.created": this.blockAudio = false; this.responding = true; this.interrupted = false; this.onText("\n"); this.state("Responding"); break;
      case "input_audio_buffer.speech_started": {
        this.interrupted = true;
        if (this.session?.provider === "grok") {
          this.blockAudio = true;
          if (this.responding) this.send({ type: "response.cancel" });
          const { itemId, heardMs } = this.playback?.interrupt() || {};
          if (itemId) this.send({ type: "conversation.item.truncate", item_id: itemId, content_index: 0, audio_end_ms: heardMs });
        }
        this.state("Listening"); break;
      }
      case "response.output_audio.delta": case "response.audio.delta":
        if (!this.blockAudio && this.playback) this.playback.enqueue(event.delta, event.item_id);
        break;
      case "response.output_audio_transcript.delta": case "response.audio_transcript.delta":
      case "response.output_text.delta": case "response.text.delta":
        this.onText(String(event.delta ?? "").slice(0, 2000)); break;
      case "response.function_call_arguments.done": this.tools?.add(event); break;
      case "response.done":
        this.responding = false;
        this.tools?.done(!this.interrupted && event.response?.status !== "cancelled" && event.response?.status !== "failed")
          .catch(() => this.stop("Tool result could not be delivered", true));
        this.state(this.muted ? "Microphone muted" : "Listening"); break;
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
    } catch { /* Local authority expires even if the cleanup request cannot arrive. */ }
  }
  async stop(label = "Stopped", error = false) {
    ++this.epoch; this.active = false; this.ready = false;
    clearTimeout(this.readyTimer); clearTimeout(this.limitTimer); this.abort?.abort();
    this.stream?.getTracks().forEach((track) => { track.onended = null; track.stop(); }); this.stream = null;
    if (this.channel) {
      this.channel.onopen = this.channel.onclose = this.channel.onerror = this.channel.onmessage = null;
      this.channel.close(); this.channel = null;
    }
    this.peer?.close(); this.peer = null;
    if (this.audio) { this.audio.pause(); this.audio.srcObject = null; this.audio = null; }
    this.playback?.interrupt(); this.playback = null;
    for (const node of [this.source, this.capture, this.silent]) { if (node) node.disconnect(); }
    if (this.capture) this.capture.port.onmessage = null;
    this.source = this.capture = this.silent = null;
    const context = this.context; this.context = null;
    const session = this.session; this.session = null; this.tools = null;
    this.state(label, error);
    await Promise.allSettled([context?.close(), this.releaseSession(session)]);
  }
}

export async function mountNativeConversation(doc = document) {
  const start = doc.getElementById("voiceStart"), stop = doc.getElementById("voiceStop"), mute = doc.getElementById("voiceMute");
  const provider = doc.getElementById("voiceProvider"), voice = doc.getElementById("voiceVoice");
  const status = doc.getElementById("voiceStatus"), caption = doc.getElementById("voiceCaption");
  const conversation = new NativeConversation({
    onState: (state) => {
      status.textContent = state.label; status.classList.toggle("err", state.error);
      start.disabled = state.active; stop.disabled = !state.active; mute.disabled = !state.ready;
      provider.disabled = voice.disabled = state.active;
      mute.textContent = state.muted ? "Unmute" : "Mute"; mute.setAttribute("aria-pressed", String(state.muted));
    },
    onText: (text) => { caption.textContent = (caption.textContent + text).slice(-2000); },
  });
  start.addEventListener("click", () => { caption.textContent = ""; conversation.start(provider.value, voice.value); });
  stop.addEventListener("click", () => conversation.stop());
  mute.addEventListener("click", () => conversation.mute());
  doc.addEventListener("keydown", (event) => { if (event.key === "Escape" && conversation.active) conversation.stop(); });
  doc.addEventListener("visibilitychange", () => { if (doc.hidden && conversation.active) conversation.stop("Stopped when the tab became hidden"); });
  globalThis.addEventListener("pagehide", () => conversation.stop());
  try {
    const response = await fetch("/voice/capabilities", { cache: "no-store" });
    if (!response.ok) throw new Error("Voice broker unavailable");
    const caps = await response.json();
    let choices = [];
    for (const spec of caps.providers) {
      const option = doc.createElement("option"); option.value = spec.id;
      option.textContent = `${spec.id === "grok" ? "Grok native voice" : "OpenAI WebRTC"}${spec.configured ? "" : " · key needed"}`;
      option.disabled = !spec.configured; provider.append(option);
      if (spec.configured) choices.push(spec);
    }
    const chooseVoice = () => {
      voice.replaceChildren();
      for (const name of caps.providers.find((spec) => spec.id === provider.value)?.voices || []) {
        const option = doc.createElement("option"); option.value = name;
        option.textContent = name[0].toUpperCase() + name.slice(1); voice.append(option);
      }
    };
    provider.addEventListener("change", chooseVoice);
    if (choices.length) { provider.value = choices[0].id; chooseVoice(); start.disabled = false; status.textContent = "Ready · microphone off"; }
    else { status.textContent = "Configure XAI_API_KEY or OPENAI_API_KEY on the server to enable native voice"; }
  } catch { status.textContent = "Native voice broker unavailable"; }
  return conversation;
}

if (typeof document !== "undefined") mountNativeConversation();
