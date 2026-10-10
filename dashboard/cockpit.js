// Starlight Voice console — polls /status and renders live operator state.
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

function renderLoop(s = {}) {
  const rows = [
    ["STT", s.stt_engine],
    ["LLM", `${s.llm_model} @ ${s.llm_fast_provider}`],
    ["TTS", s.tts_engine],
  ];
  $("loopKv").innerHTML = rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("");
}

function renderLanes(variants = []) {
  $("lanes").innerHTML = variants
    .map((v) => {
      const ok = v.key_live;
      const hypo = v.latency_hypothesis ? `<span class="hypo">${esc(v.latency_hypothesis)}</span>` : "";
      return `<li class="lane"><span><span class="name">${esc(v.label)}</span><br><span class="engine">${esc(
        v.engine
      )}</span>${hypo}</span>
        <span class="pill ${ok ? "ok" : "blocked"}">${ok ? "key configured" : "key needed"}</span></li>`;
    })
    .join("");
}

function renderAdapters(adapters = {}) {
  $("adapters").innerHTML = Object.entries(adapters)
    .map(([k, v]) => `<li><span>${esc(k)}</span><span class="${v ? "yes" : "no"}">${v ? "●" : "○"}</span></li>`)
    .join("");
}

function renderLedger(runs = []) {
  if (!runs.length) {
    $("ledger").innerHTML = `<li class="empty">No recorded dispatches. Native voice dispatch awaits managed intake.</li>`;
    return;
  }
  $("ledger").innerHTML = runs
    .slice()
    .reverse()
    .map((r) => {
      const tier = (r.tier || "?").toUpperCase();
      return `<li><span class="tier ${esc(tier)}">${esc(tier)}</span>
        <span class="st">${esc(r.status)}</span>
        <span class="task" title="${esc(r.task)}">${esc(r.task)}</span>
        <span class="st">${esc((r.target || "").split(" ")[0])}</span></li>`;
    })
    .join("");
}

// Animate the first-audio budget number toward its target (easeOutCubic).
let budgetShown = null;
function renderBudget(ms) {
  const node = $("budget");
  if (ms == null) {
    node.textContent = "—";
    return;
  }
  if (reduce || budgetShown === ms) {
    node.textContent = `≤ ${ms} ms`;
    budgetShown = ms;
    return;
  }
  const from = budgetShown ?? 0;
  budgetShown = ms;
  const start = performance.now();
  const tick = (now) => {
    const t = Math.min(1, (now - start) / 700);
    const eased = 1 - Math.pow(1 - t, 3);
    node.textContent = `≤ ${Math.round(from + (ms - from) * eased)} ms`;
    if (t < 1) requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}

// Header waveform — the operator's "listening" signature; amplitude rises when live.
let live = false;
function startWave() {
  const canvas = $("topwave");
  if (!canvas || reduce) return;
  const ctx = canvas.getContext("2d");
  let w = 0;
  let h = 0;
  const resize = () => {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    w = canvas.clientWidth;
    h = canvas.clientHeight;
    canvas.width = Math.floor(w * dpr);
    canvas.height = Math.floor(h * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  };
  resize();
  window.addEventListener("resize", resize);
  let amp = 0;
  const draw = (time) => {
    ctx.clearRect(0, 0, w, h);
    amp += ((live ? 1 : 0.25) - amp) * 0.05; // ease toward target amplitude
    const midY = h * 0.5;
    ctx.beginPath();
    for (let x = 0; x <= w; x += 5) {
      const k = x / w;
      const env = Math.sin(k * Math.PI);
      const y =
        midY +
        Math.sin(k * 30 + time * 0.002) * 7 * env * amp +
        Math.sin(k * 13 - time * 0.0013) * 11 * env * amp;
      x === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    }
    const grad = ctx.createLinearGradient(0, 0, w, 0);
    grad.addColorStop(0, "rgba(110,92,255,0)");
    grad.addColorStop(0.5, "rgba(110,92,255,0.45)");
    grad.addColorStop(1, "rgba(224,182,86,0)");
    ctx.strokeStyle = grad;
    ctx.lineWidth = 1.5;
    ctx.stroke();
    requestAnimationFrame(draw);
  };
  requestAnimationFrame(draw);
}

async function poll() {
  try {
    const s = await (await fetch("/status", { cache: "no-store" })).json();
    renderLoop(s.settings);
    renderLanes(s.variants);
    renderAdapters(s.adapters);
    renderLedger(s.runs);
    renderBudget(s.first_audio_budget_ms ?? null);
    const up = false; // Configuration is not a runtime health result.
    const gw = $("gateway");
    gw.textContent = `Native projection: ${s.native_voice?.memory_projection_configured ? "configured · unverified" : "unconfigured"}`;
    gw.className = `gateway ${up ? "up" : "down"}`;
    $("liveState").className = "live ok";
    $("liveLabel").textContent = "Server connected";
    $("err").textContent = s.error || "";
    live = true;
  } catch (e) {
    $("liveState").className = "live err";
    $("liveLabel").textContent = "offline";
    $("err").textContent = String(e);
    live = false;
  }
}

startWave();
poll();
setInterval(poll, 4000);
