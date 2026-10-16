# Starlight Voice

This repo is the local voice operator. One harness writes one branch, `agent/<harness>/<scope>`.

The open gesture is two claps. One clap stays shut. Speech stays shut. A second pair of claps, or 45 seconds of quiet, closes the xAI socket.

`listen` speaks through `grok-voice-latest`. A work request calls `meta_harness`, which asks the estate dispatch table and stops. The operator does not spawn a coding seat, merge, send, or spend.

The voice socket reads `XAI_API_KEY` from Infisical EU, project `5578966b-0c74-4253-9a3c-bace21d97b24`, environment `dev`. It uses that value only when it starts with `xai-`. OpenRouter keys are ignored. The Grok CLI login is the fallback. The socket stays closed after 15 minutes in a UTC day, 10 minutes in one call, or 45 seconds of quiet. `x_search` stays off unless `STARLIGHT_XVOICE_X_SEARCH=1`. Never commit keys. The voice is `ara`. Reasoning is `high`.

The live page is `http://127.0.0.1:8765/live`. It watches the clap listener. It does not open a second microphone. Stop the current listen before starting the next one.

A question about the desk or this machine calls `estate_lookup`. That reads the session store and the command-center desk files. Public web search stays the xAI `web_search` tool. Neither one starts work.

Checks for this slice: `python -m pytest sidecar/tests/test_clap.py sidecar/tests/test_x_voice.py sidecar/tests/test_session_store.py` from the repo root, with `PYTHONPATH=sidecar/src`.
