/* ============================================================================
   NeuralField — a living, human-brain-inspired node network on a 2D canvas.
   Zero dependencies. Shared by the landing hero (mode: "brain") and the operator
   console header (mode: "compact").

   Anatomy: two cerebrum lobes of clustered nodes (a longitudinal fissure between),
   a brain stem feeding a spine of nodes, and a heart node that beats — each beat
   sends a signal up the spine into the cerebrum. Synapses fire on a pulse and the
   signal propagates edge to edge, flashing nodes gold. At rest the network glows
   bioluminescent teal.

   Performance: node count capped, dpr<=2, paused offscreen. prefers-reduced-motion
   renders a single still frame (no animation loop).
   ============================================================================ */
(function (global) {
  "use strict";

  var COL = {
    synapse: [79, 227, 208], // teal — the resting network
    voltage: [110, 92, 255], // the spark
    fire: [224, 182, 86], // a synapse firing
    dim: [95, 113, 128], // quiet axons
  };
  function rgba(c, a) {
    return "rgba(" + c[0] + "," + c[1] + "," + c[2] + "," + a + ")";
  }
  var TAU = Math.PI * 2;

  function NeuralField(canvas, opts) {
    opts = opts || {};
    var mode = opts.mode || "brain";
    var ctx = canvas.getContext("2d");
    var reduce = global.matchMedia && global.matchMedia("(prefers-reduced-motion: reduce)").matches;

    var w = 0,
      h = 0,
      dpr = 1,
      raf = 0,
      running = false;
    var nodes = [],
      edges = [],
      adj = [],
      signals = [],
      heartIdx = -1,
      spineTopIdx = -1,
      lastFire = 0,
      lastBeat = 0;

    var CFG =
      mode === "compact"
        ? { perLobe: 9, spine: 4, fireEvery: 1700, beatEvery: 1500, nodeR: 1.6, edgeMax: 4200, sigCap: 26, drift: 0.6 }
        : { perLobe: 24, spine: 8, fireEvery: 1500, beatEvery: 1400, nodeR: 1.9, edgeMax: 9000, sigCap: 60, drift: 1.0 };

    // ── layout ───────────────────────────────────────────────────────────────
    function node(hx, hy, type) {
      return { hx: hx, hy: hy, x: hx, y: hy, type: type, lit: 0, phase: Math.random() * TAU, spd: 0.4 + Math.random() * 0.6, amp: 4 + Math.random() * 6 };
    }

    function sampleLobe(cx, cy, rx, ry, n, fissureX, side) {
      var out = [];
      var tries = 0;
      while (out.length < n && tries < n * 6) {
        tries++;
        var r = Math.sqrt(Math.random());
        var a = Math.random() * TAU;
        var x = cx + rx * r * Math.cos(a);
        var y = cy + ry * r * Math.sin(a) * 0.92; // slight vertical squash
        // carve the longitudinal fissure between hemispheres
        if (side < 0 && x > fissureX) continue;
        if (side > 0 && x < fissureX) continue;
        out.push(node(x, y, "cerebrum"));
      }
      return out;
    }

    function layout() {
      nodes = [];
      edges = [];
      adj = [];
      signals = [];
      var cx = w * 0.5;

      if (mode === "compact") {
        // a horizontal mini-brain: a cluster on the left, a short axon to a heart on the right
        var cyc = h * 0.5;
        var bw = Math.min(w * 0.16, h * 0.9);
        nodes = nodes.concat(sampleLobe(w * 0.18, cyc, bw * 0.6, h * 0.34, CFG.perLobe, w * 0.18, -1));
        nodes = nodes.concat(sampleLobe(w * 0.3, cyc, bw * 0.6, h * 0.34, CFG.perLobe, w * 0.3, 1));
        spineTopIdx = nodes.length - 1;
        var sx0 = w * 0.42;
        for (var i = 0; i < CFG.spine; i++) {
          nodes.push(node(sx0 + (w * 0.4 * i) / CFG.spine, cyc + Math.sin(i) * 3, "spine"));
        }
        heartIdx = nodes.length;
        nodes.push(node(w * 0.86, cyc, "heart"));
      } else {
        // full brain: a cerebrum crowning the top, a spine descending THROUGH the
        // content, and a heart near the bottom — the anatomy frames the page.
        var cy = h * 0.26;
        var S = Math.min(w * 0.5, h * 1.5);
        var bw = S * 0.52;
        var bh = S * 0.28;
        var lobeRx = bw * 0.6;
        nodes = nodes.concat(sampleLobe(cx - bw * 0.42, cy, lobeRx, bh, CFG.perLobe, cx - bw * 0.04, -1));
        nodes = nodes.concat(sampleLobe(cx + bw * 0.42, cy, lobeRx, bh, CFG.perLobe, cx + bw * 0.04, 1));
        // brain stem + spine descending from the base of the cerebrum
        var stemY = cy + bh * 0.95;
        spineTopIdx = nodes.length;
        var spineBottom = h * 0.8;
        for (var s = 0; s < CFG.spine; s++) {
          var f = s / (CFG.spine - 1);
          nodes.push(node(cx + Math.sin(f * 3.2) * (bw * 0.07), stemY + (spineBottom - stemY) * f, "spine"));
        }
        // the heart — offset right, near the base of the page
        heartIdx = nodes.length;
        nodes.push(node(cx + bw * 0.26, h * 0.9, "heart"));
      }
      buildEdges();
    }

    function buildEdges() {
      adj = nodes.map(function () {
        return [];
      });
      var addEdge = function (i, j) {
        if (i === j) return;
        for (var e = 0; e < adj[i].length; e++) if (adj[i][e] === j) return;
        edges.push([i, j]);
        adj[i].push(j);
        adj[j].push(i);
      };
      // proximity edges among cerebrum nodes (k-nearest within range)
      for (var i = 0; i < nodes.length; i++) {
        if (nodes[i].type !== "cerebrum") continue;
        var near = [];
        for (var j = 0; j < nodes.length; j++) {
          if (j === i || nodes[j].type !== "cerebrum") continue;
          var dx = nodes[i].hx - nodes[j].hx,
            dy = nodes[i].hy - nodes[j].hy;
          var d2 = dx * dx + dy * dy;
          if (d2 < CFG.edgeMax) near.push([d2, j]);
        }
        near.sort(function (a, b) {
          return a[0] - b[0];
        });
        for (var k = 0; k < Math.min(3, near.length); k++) addEdge(i, near[k][1]);
      }
      // a few cross-hemisphere axons (corpus callosum)
      var lefts = [],
        rights = [];
      for (var n = 0; n < nodes.length; n++) {
        if (nodes[n].type !== "cerebrum") continue;
        (nodes[n].hx < w * 0.5 ? lefts : rights).push(n);
      }
      var bridges = mode === "compact" ? 2 : 4;
      for (var b = 0; b < bridges && lefts.length && rights.length; b++) {
        addEdge(lefts[(b * 7) % lefts.length], rights[(b * 5) % rights.length]);
      }
      // spine chain, stem -> spine, spine -> heart
      var spineIdx = [];
      for (var p = 0; p < nodes.length; p++) if (nodes[p].type === "spine") spineIdx.push(p);
      for (var q = 0; q < spineIdx.length - 1; q++) addEdge(spineIdx[q], spineIdx[q + 1]);
      if (spineIdx.length) {
        if (spineTopIdx >= 0 && spineTopIdx < nodes.length) addEdge(spineTopIdx, spineIdx[0]);
        if (heartIdx >= 0) addEdge(spineIdx[spineIdx.length - 1], heartIdx);
      }
    }

    // ── signals (synaptic propagation) ─────────────────────────────────────────
    function emit(from, to, color, depth) {
      if (signals.length >= CFG.sigCap) return;
      signals.push({ from: from, to: to, t: 0, spd: 0.022 + Math.random() * 0.02, color: color, depth: depth });
    }
    function fireFrom(idx, color, depth) {
      var ns = adj[idx] || [];
      for (var i = 0; i < ns.length; i++) {
        if (Math.random() < (color === COL.fire ? 0.85 : 0.6)) emit(idx, ns[i], color, depth);
      }
    }

    // ── frame ───────────────────────────────────────────────────────────────
    function drawEdges() {
      for (var e = 0; e < edges.length; e++) {
        var a = nodes[edges[e][0]],
          b = nodes[edges[e][1]];
        var dx = a.x - b.x,
          dy = a.y - b.y;
        var d = Math.sqrt(dx * dx + dy * dy);
        var lit = Math.max(a.lit, b.lit);
        // structural edges (the spine + heart link) never fade out — the anatomy
        // must read as continuously connected, crown to heart.
        var structural = a.type !== "cerebrum" || b.type !== "cerebrum";
        var alpha = structural ? 0.2 + lit * 0.45 : Math.max(0, 0.26 - d / 3600) + lit * 0.35;
        if (alpha <= 0) continue;
        ctx.strokeStyle = rgba(lit > 0.1 ? COL.fire : COL.synapse, alpha);
        ctx.lineWidth = (structural ? 1.3 : 1) + lit;
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(b.x, b.y);
        ctx.stroke();
      }
    }

    function drawNode(p) {
      var base = p.type === "heart" ? COL.fire : COL.synapse;
      var col = p.lit > 0.02 ? COL.fire : base;
      var r = (p.type === "heart" ? CFG.nodeR * 2.4 : CFG.nodeR) * (1 + p.lit * 0.8) * p.beatScale;
      var glow = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, r * (6 + p.lit * 8));
      glow.addColorStop(0, rgba(col, 0.5 + p.lit * 0.4));
      glow.addColorStop(1, rgba(col, 0));
      ctx.fillStyle = glow;
      ctx.beginPath();
      ctx.arc(p.x, p.y, r * (6 + p.lit * 8), 0, TAU);
      ctx.fill();
      ctx.fillStyle = rgba(col, 0.92); // bright core (gold when firing/heart, else teal)
      ctx.beginPath();
      ctx.arc(p.x, p.y, r, 0, TAU);
      ctx.fill();
    }

    function frame(time) {
      if (!running) return;
      if (!lastFire) {
        lastFire = time;
        lastBeat = time;
      }
      ctx.clearRect(0, 0, w, h);

      // periodic synaptic fire from a random cerebrum node
      if (time - lastFire > CFG.fireEvery) {
        lastFire = time;
        var pool = [];
        for (var i = 0; i < nodes.length; i++) if (nodes[i].type === "cerebrum") pool.push(i);
        if (pool.length) {
          var src = pool[(Math.random() * pool.length) | 0];
          nodes[src].lit = 1;
          fireFrom(src, COL.synapse, 0);
        }
      }
      // heartbeat — send a gold signal up the spine into the brain
      var beatScale = 1;
      var beatPhase = ((time - lastBeat) % CFG.beatEvery) / CFG.beatEvery;
      if (time - lastBeat > CFG.beatEvery) {
        lastBeat = time;
        if (heartIdx >= 0) {
          nodes[heartIdx].lit = 1;
          fireFrom(heartIdx, COL.fire, 0);
        }
      }
      // a quick double-thump envelope for the heart node scale
      beatScale = 1 + 0.18 * Math.max(0, Math.sin(beatPhase * Math.PI * 2)) + 0.1 * Math.max(0, Math.sin(beatPhase * Math.PI * 4));

      // update + draw nodes
      for (var n = 0; n < nodes.length; n++) {
        var p = nodes[n];
        p.x = p.hx + Math.sin(time * 0.0006 * p.spd + p.phase) * p.amp * CFG.drift;
        p.y = p.hy + Math.cos(time * 0.0006 * p.spd + p.phase) * p.amp * 0.6 * CFG.drift;
        p.lit *= 0.95;
        p.beatScale = p.type === "heart" ? beatScale : 1;
      }
      drawEdges();

      // advance signals (packets travelling along edges)
      for (var s = signals.length - 1; s >= 0; s--) {
        var sig = signals[s];
        sig.t += sig.spd;
        var a = nodes[sig.from],
          b = nodes[sig.to];
        var x = a.x + (b.x - a.x) * sig.t,
          y = a.y + (b.y - a.y) * sig.t;
        var pr = 2.2;
        var g = ctx.createRadialGradient(x, y, 0, x, y, pr * 6);
        g.addColorStop(0, rgba(sig.color, 0.9));
        g.addColorStop(1, rgba(sig.color, 0));
        ctx.fillStyle = g;
        ctx.beginPath();
        ctx.arc(x, y, pr * 6, 0, TAU);
        ctx.fill();
        if (sig.t >= 1) {
          b.lit = 1;
          if (sig.depth < (mode === "compact" ? 2 : 3)) fireFrom(sig.to, sig.color, sig.depth + 1);
          signals.splice(s, 1);
        }
      }

      for (var d = 0; d < nodes.length; d++) drawNode(nodes[d]);

      raf = global.requestAnimationFrame(frame);
    }

    function still() {
      // one elegant static frame for reduced-motion / no-rAF contexts
      ctx.clearRect(0, 0, w, h);
      for (var n = 0; n < nodes.length; n++) nodes[n].beatScale = 1;
      // light a handful of nodes gold so the "firing" identity still reads, statically
      for (var i = 0; i < nodes.length; i += 7) nodes[i].lit = 0.6;
      drawEdges();
      for (var d = 0; d < nodes.length; d++) drawNode(nodes[d]);
    }

    // ── lifecycle ─────────────────────────────────────────────────────────────
    function resize() {
      dpr = Math.min(global.devicePixelRatio || 1, 2);
      w = canvas.clientWidth;
      h = canvas.clientHeight;
      if (!w || !h) return;
      canvas.width = Math.floor(w * dpr);
      canvas.height = Math.floor(h * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      layout();
      if (reduce) still();
    }

    function start() {
      if (reduce || running) return;
      running = true;
      lastFire = 0;
      lastBeat = 0;
      raf = global.requestAnimationFrame(frame);
    }
    function stop() {
      running = false;
      global.cancelAnimationFrame(raf);
    }

    resize();
    global.addEventListener("resize", resize);
    // pause when scrolled offscreen — don't burn cycles the user can't see
    if ("IntersectionObserver" in global && !reduce) {
      new IntersectionObserver(function (ents) {
        ents[0].isIntersecting ? start() : stop();
      }).observe(canvas);
    } else if (!reduce) {
      start();
    }

    return { start: start, stop: stop, resize: resize };
  }

  global.NeuralField = NeuralField;
})(window);
