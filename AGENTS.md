# Starlight Voice

This repo is the local voice operator. One harness writes one branch, `agent/<harness>/<scope>`.

The open gesture is two claps. One clap stays shut. Speech stays shut. A second pair of claps, or 45 seconds of quiet, closes the xAI socket.

`listen` speaks through `grok-voice-latest`. A work request calls `meta_harness`, which emits one dispatch packet and stops. It does not spawn a harness. No approval gate exists yet. The operator does not merge, send, or spend.

The voice socket takes its token in one order. First, `XAI_API_KEY` from the environment, and only when the value starts with `xai-`. Second, the Infisical EU secret of the same name, project `5578966b-0c74-4253-9a3c-bace21d97b24`, environment `dev`, and only when that value starts with `xai-`. OpenRouter keys are ignored. There is no Grok CLI login. Never commit keys. The voice is `ara`. Reasoning is `high`.

The socket stays closed after 15 minutes reserved in a UTC day, 10 minutes in one call, or 45 seconds of quiet. A reserve is written before the socket opens and a crash keeps it. It also stays closed unless `XAI_MANAGEMENT_KEY` and `XAI_TEAM_ID` can read an xAI postpaid spending limit that still has room. An unknown data class is refused. Class and dirty state come from the utterance and `git status`, never from the model, and never default to public. `x_search` stays off unless `STARLIGHT_XVOICE_X_SEARCH=1`.

The live page is `http://127.0.0.1:8765/live`. It watches the clap listener. It does not open a second microphone. Stop the current listen before starting the next one.

A question about the desk or this machine calls `estate_lookup`. That reads the session store and the command-center desk files. Public web search stays the xAI `web_search` tool. Neither one starts work.

Checks for this slice: `python -m pytest sidecar/tests/test_clap.py sidecar/tests/test_x_voice.py sidecar/tests/test_session_store.py` from the repo root, with `PYTHONPATH=sidecar/src`.
