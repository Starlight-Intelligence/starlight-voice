# Starlight Voice

This repo is the local voice operator. One harness writes one branch, `agent/<harness>/<scope>`.

The open gesture is two claps. One clap stays shut. Speech stays shut. A second pair of claps, or 45 seconds of quiet, closes the xAI socket.

`listen` speaks through `grok-voice-latest`. A work request calls `meta_harness`, which asks the estate dispatch table and stops. The operator does not spawn a coding seat, merge, send, or spend.

Keys stay in `.env.local` or the existing Grok CLI login. Never commit them.

Checks for this slice: `python -m pytest sidecar/tests/test_clap.py sidecar/tests/test_x_voice.py` from the repo root, with `PYTHONPATH=sidecar/src`.
