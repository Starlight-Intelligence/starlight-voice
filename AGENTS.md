# Starlight Voice

This repo is the local voice operator. One harness writes one branch, `agent/<harness>/<scope>`.

The open gesture is two claps. One clap stays shut. Speech stays shut. A second pair of claps, or 45 seconds of quiet, closes the xAI socket.

`listen` speaks through `grok-voice-latest`. A work request calls `meta_harness`, which emits one dispatch packet. The operator never spawns a harness; `started` stays false. The operator does not merge, send, or spend.

Tier-B gate (`approval.py`). A packet that is public, has a buildable job, and lands on a seat the Queen can run (`claude-local`, `codex-cloud`, `grok-local`) becomes a proposal, but only when the utterance names exactly one checkout under `starlight/repos` and that checkout is listed in `memory/voice/repos.allow` (one name per line, curated by Frank, gitignored). No allowlist file means nothing is proposable. A proposal queues one Queen inbox card, `voice-<id>`, when two signals land within 180 seconds: Frank's spoken go, recorded by the `confirm_handoff` tool, and a human ack that names the proposal's 4-character code, from `python -m starlight_voice approve <code>` or a same-origin `POST /api/approve {"code"}`. `approve` with no code and `GET /api/pending` only list the exact request, repo, and seat. The model has no tool that writes the ack. The request reaches the seat as a capped JSON string, never as instructions. `handoff_status` returns the lane and a PR URL, never report text. The card asks for a draft PR from `agent/<seat>/voice-<id>` and forbids merge, publish, send, spend, and delete. `handoff_status` reads the card's Queen lane back. Merge, dispatch, any non-public class, Jules, and n8n stay tier C and are never proposed.

Every dashboard POST must be same-origin JSON: Host and Origin are 127.0.0.1 or localhost on the served port, and content-type is `application/json`.

The voice socket takes its token in one order. First, the signed-in grok CLI session in `~/.grok/auth.json` while `expires_at` is still in the future. Second, `XAI_API_KEY` from the environment, and only when the value starts with `xai-`. Third, the Infisical EU secret of the same name, project `5578966b-0c74-4253-9a3c-bace21d97b24`, environment `dev`, and only when that value starts with `xai-`. OpenRouter keys are ignored. Never commit keys. The voice is `ara`. Reasoning is `high`.

The socket stays closed after 15 minutes reserved in a UTC day, 10 minutes in one call, or 45 seconds of quiet. A reserve is written before the socket opens and a crash keeps it. An `xai-` key also stays closed unless `XAI_MANAGEMENT_KEY` and `XAI_TEAM_ID` can read an xAI postpaid spending limit that still has room, or, when the postpaid limit is zero, a prepaid credit balance, and only once Frank has set `STARLIGHT_XVOICE_PREPAID_BOUNDED=1` after checking that auto top-up is off or capped in the xAI console (no API reports it). The grok CLI session skips that management check. It does not enable `web_search` or `x_search`. An unknown data class is refused. Class and dirty state come from the utterance and `git status`. A model claim cannot set or raise them, and a claim that disagrees is refused. They never default to public. `x_search` stays off unless `STARLIGHT_XVOICE_X_SEARCH=1`.

The live page is `http://127.0.0.1:8765/live`. It watches the clap listener. It does not open a second microphone. Stop the current listen before starting the next one.

A question about the desk or this machine calls `estate_lookup`. That reads the session store and the command-center desk files. Public web search stays the xAI `web_search` tool. Neither one starts work.

Checks for this slice: `python -m pytest sidecar/tests/test_clap.py sidecar/tests/test_x_voice.py sidecar/tests/test_session_store.py sidecar/tests/test_approval.py sidecar/tests/test_approval_wiring.py sidecar/tests/test_dashboard_server.py sidecar/tests/test_cup_cases.py` from the repo root, with `PYTHONPATH=sidecar/src`.
