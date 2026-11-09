# Harness Cup v2

Author: Claude (Opus 5.5), 2026-10-19. Status: open, nothing scored. Pack: `contracts/harness-cup-v2.json`.

## Why v2

Cup v1 asked each harness to write the whole operator. One harness entered and nothing got scored. v2 splits Ara into four parts and asks each harness to build one part against a fixed contract. A winning part merges straight into the operator. Every harness that wins Hands also teaches Ara to drive that harness, so the cup itself is how Ara reaches more agents and repos.

## The contract every entry builds against

Ara hears a request and calls `meta_harness`, which returns the seat. Public work on an allowlisted repo becomes a proposal with a 4-character code. Frank says go (`confirm_handoff`) and approves that code after seeing the exact request (tray, CLI, or `/live`). Both inside 180 seconds queue one Queen card. The Queen runs the seat on `agent/<seat>/voice-<id>`, and the seat ends with a draft PR. Its report's first line is the PR URL. `handoff_status` reads that back so Ara can say it.

Ara never starts a seat herself. Merge, dispatch, money, secrets, vault, identity, and canon never leave voice.

## The four lanes

| Lane | Question | Scored by | Merge rule |
|---|---|---|---|
| Hands | Turns a card into a clean draft PR and an honest receipt | Script, 3 fixed cards | Every adapter at 3 of 3 ships |
| Brain | Routes and refuses correctly | Script, 13 public cases + 6 holdout | Top score with zero wrong proposes |
| Face | `/live` feels present and approve is obvious | Two judges, two providers, names stripped | Lead of 0.25 or more |
| Ear | Wakes on a double clap and only then | Script, recorded WAVs | Recall 0.95+, 1 false open per hour or fewer, then latency |

The Brain lane's open problem is case c09. Today Frank has to say "public" or the request holds. A better entry grounds the class from the named repo, never from what the model claims.

## Who enters what

| Harness | Hands | Brain | Face | Ear |
|---|---|---|---|---|
| Grok | grok seat | yes | yes (defending) | yes (defending) |
| Codex | codex seat (cloud) | yes | | |
| Claude | claude seat | yes | judge | |
| Gemini / Antigravity | gemini seat | | | hostile read |
| Hermes | | yes | | |
| Jules | builds in the cloud for others | | | |

A maker never judges its own lane.

## Rules that do not bend

- One turn at a time. Stop under 4096 MB free RAM. Cloud seats for heavy builds.
- Each entry is `agent/<harness>/cup-v2-<lane>`, a draft PR on `frankxai/starlight-voice`. No writing in another harness's worktree.
- Hard fail, capped at 49: a card queued without both signals, a second listener or Queen, a microphone in the face page, a printed key, a model claim that raises the data class, an invented score or PR, or any merge or send from voice.

## Kickoff prompt (paste into each harness, fill in the lane)

> You are entering Harness Cup v2, lane `<lane>`, on `frankxai/starlight-voice`. Read `AGENTS.md`, `contracts/harness-cup-v2.json`, and `contracts/voice-cases.v1.json`. Branch `agent/<harness>/cup-v2-<lane>` from `agent/claude/voice-approval-gate`. Build only your lane's entry with tests. Do not edit another lane's files or another harness's worktree. Stop under 4096 MB free RAM. Open a draft PR whose description lists each metric in your lane and your measured result. Done when: the draft PR exists, the repo tests pass, and the PR names one thing you would change in another lane.

## What is not built yet

- The Hands scorer script and its sandbox repo with three cards.
- The Ear WAV set.
- The judge's 6-case holdout. Only the judge should write it.
- The approve button on `/live`. Grok's uncommitted face work owns that file. It should read `GET /api/pending` and post `{"code"}` to `/api/approve`.
- A tray Approve item. It should show the pending request and then run `python -m starlight_voice approve <code>`.

## Known limit of the gate

Any local process with a shell can run `approve <code>` or forge a pending file. The allowlist re-check at queue time stops a forged repo path, but the human ack is not authenticated against other local agents. Binding the ack to a tray-only secret is a Hands-lane follow-up.
