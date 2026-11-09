/* ============================================================================
   Starlight Voice — landing motion. Zero dependencies.
   Principles: progressive enhancement (content lives without JS), and a hard
   reduced-motion bail so the canvas + scroll choreography never fight a user
   who asked for stillness.
   ========================================================================== */
(() => {
  "use strict";
  const doc = document.documentElement;
  doc.classList.remove("no-js");
  doc.classList.add("js");

  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* ── scroll progress rail + condensing nav ─────────────────────────────── */
  const fill = document.getElementById("scrollFill");
  const nav = document.getElementById("nav");
  let ticking = false;
  const onScroll = () => {
    const h = doc.scrollHeight - doc.clientHeight;
    const p = h > 0 ? (window.scrollY / h) * 100 : 0;
    if (fill) fill.style.width = p + "%";
    if (nav) nav.classList.toggle("scrolled", window.scrollY > 24);
    ticking = false;
  };
  window.addEventListener(
    "scroll",
    () => {
      if (!ticking) {
        window.requestAnimationFrame(onScroll);
        ticking = true;
      }
    },
    { passive: true }
  );
  onScroll();

  /* ── reveal-on-scroll (+ pipeline pulse + count-up trigger) ────────────── */
  const reveals = Array.from(document.querySelectorAll(".reveal"));
  if (reduce || !("IntersectionObserver" in window)) {
    reveals.forEach((el) => el.classList.add("in"));
    document.getElementById("pipeline")?.classList.add("lit");
    runCounts();
  } else {
    const io = new IntersectionObserver(
      (entries) => {
        entries.forEach((e) => {
          if (!e.isIntersecting) return;
          e.target.classList.add("in");
          if (e.target.id === "pipeline") e.target.classList.add("lit");
          if (e.target.querySelector?.(".count") || e.target.classList.contains("count")) {
            runCounts(e.target);
          }
          io.unobserve(e.target);
        });
      },
      { threshold: 0.18, rootMargin: "0px 0px -8% 0px" }
    );
    reveals.forEach((el) => io.observe(el));
    // pipeline may not carry .reveal on its own — observe explicitly
    const pipe = document.getElementById("pipeline");
    if (pipe && !pipe.classList.contains("reveal")) io.observe(pipe);
  }

  /* ── count-up numbers (honours the data-to / data-suffix on .count) ────── */
  function runCounts(scope) {
    const root = scope && scope.querySelectorAll ? scope : document;
    root.querySelectorAll(".count:not([data-done])").forEach((node) => {
      const to = parseFloat(node.dataset.to || "0");
      const suffix = node.dataset.suffix || "";
      node.dataset.done = "1";
      if (reduce) {
        node.textContent = to + suffix;
        return;
      }
      const dur = 1100;
      const start = performance.now();
      const tick = (now) => {
        const t = Math.min(1, (now - start) / dur);
        const eased = 1 - Math.pow(1 - t, 3); // easeOutCubic
        node.textContent = Math.round(to * eased) + suffix;
        if (t < 1) requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
    });
  }

  /* ── pointer tilt on cards (subtle, GPU-cheap, pointer-only) ───────────── */
  if (!reduce && window.matchMedia("(hover: hover) and (pointer: fine)").matches) {
    document.querySelectorAll("[data-tilt]").forEach((card) => {
      card.addEventListener("pointermove", (ev) => {
        const r = card.getBoundingClientRect();
        const x = (ev.clientX - r.left) / r.width - 0.5;
        const y = (ev.clientY - r.top) / r.height - 0.5;
        card.style.transform = `perspective(800px) rotateX(${(-y * 5).toFixed(2)}deg) rotateY(${(
          x * 5
        ).toFixed(2)}deg) translateY(-4px)`;
      });
      card.addEventListener("pointerleave", () => {
        card.style.transform = "";
      });
    });
  }

  /* ── hero: the living neural field (brain + spine + heart) ──────────────── */
  // NeuralField (site/neural.js) owns the canvas: layout, firing, heartbeat,
  // offscreen-pause, and the reduced-motion still frame. We just hand it the node.
  const canvas = document.getElementById("heroCanvas");
  if (canvas && typeof window.NeuralField === "function") {
    window.NeuralField(canvas, { mode: "brain" });
  }
})();
