# Starlight Voice repository instructions

This repository owns the Python voice sidecar, optional Tauri shell, standalone
dashboard and reusable native browser client. Read `README.md` and the relevant
sections of `docs/ARCHITECTURE-2026-10-10.md` before changing those paths.

- Keep native Grok/OpenAI browser transports separate from the optional Pipecat
  component graph. Native browser voice requires no local speech model.
- Keep sidecar imports, configuration discovery and headless assembly safe without
  optional audio/model dependencies. The local component conversation requires
  speech boundaries; its `voice-vad` extra is optional and explicitly selected.
- The native broker can produce unsaved mission drafts. Component tools allow
  scoped reads only. Neither route admits coding work, grants approval or launches
  a harness. Reuse the existing managed intake before claiming execution.
- Preserve vendored-client provenance when integrating with Command Center.
  Observations, graph assembly and synthetic tests establish their stated scope;
  live authentication, audible latency, voice preference and RAM need direct proof.
- Do not start audio, local models, servers or provider inference without the
  current task's authorization and machine admission. Keep secrets out of source,
  transcripts, test output and committed observations.

Python requires 3.12 or newer. With the existing development dependencies installed:

```text
python -m ruff check sidecar dashboard/server.py benchmarks
python -m pytest sidecar/tests -q
node --test dashboard/tests/*.test.mjs
```

`.github/workflows/ci.yml` also verifies Pipecat 1.3.0 and 1.12.0 on the actual SDK,
plus the Windows Rust release build. Optional SDK tests may skip on a bare local
interpreter; report that explicitly and obtain hosted results before handoff.
