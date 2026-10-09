# AI Interview — Integration & Status

This document covers the live AI interview feature added on top of the existing
DEDAN Remote discovery/scoring/application workflows. The backend interview
domain (`interview/`) and the `/api/interview/*` router were already implemented
and are real, not stubs. This change adds the missing **client** layer and proves
the end-to-end contract with automated tests.

## What already existed (verified, unchanged)

- `interview/engine.py` — role-specific plan generation, per-answer evaluation,
  ranked report. The plan and per-answer scores never cross the wire.
- `interview/followup.py` — adaptive follow-up selection, depth adjustment,
  bounded budget, coverage tracking.
- `interview/room.py` / `room_state.py` — the session state machine
  (PREPARING → … → COMPLETED/COMPLETED_FAILED), presence/phase mapping,
  ownership enforcement, privacy statement.
- `interview/rubric.py` — evidence-based scoring dimensions, speech measurement.
- `interview/room_store.py` — SQLite persistence; startup retention purge.
- `interview/provider.py` — one adapter over any OpenAI-compatible
  `/chat/completions`. **Hard rule:** if no provider is configured it raises
  `ProviderUnavailable` — it never fabricates a canned interview.
- `api/routers/interview.py` — full lifecycle endpoints, SSE event stream via a
  one-use ticket, rate limits, auth + ownership on every route.

## What this change added

### Frontend clients (wired to the real API)

- `frontend/interview-room.html` — Stage 7 full-screen interview room.
  - Top bar: identity, role, stage, elapsed clock, live connection status.
  - AI interviewer presence with listening/speaking/thinking/evaluating states
    (driven by `interviewer_state` from the server). Avatar is explicitly
    labelled "AI … not a human".
  - Current question card, follow-up badge, competency chips (from the posting).
  - Live transcript panel + on-demand full transcript from `/transcript`.
  - Candidate tile with optional camera; mic mute; browser-native speech input
    (`SpeechRecognition`) and spoken interviewer voice (`speechSynthesis`),
    both toggleable. No audio is recorded or transmitted — it is transcribed in
    the browser, matching the server's privacy flags.
  - Typed-answer fallback; repeat-question; pause/resume; end-with-confirmation.
  - SSE events via a one-use ticket; automatic reconnect; inline error/recovery
    banner; honest boot screen when unauthenticated or unconfigured.
- `frontend/interview-results.html` — Stage 10 results page.
  - Reads `/feedback` (stored report) and `/transcript`.
  - Overall score + band; competency breakdown that shows **"Not assessed"**
    instead of inventing a score where evidence was thin.
  - Strengths, improvements, per-question evidence, recommended practice,
    full transcript, practice-again.
  - States plainly that this is an AI practice assessment, not an employer
    decision, and that no company received the results.
- `frontend/job-detail.html` — "Start AI interview" now opens the room client
  with the opportunity id (`interview-room.html?job=…`).

### Tests

- `tests/test_interview_client_contract.py` — 8 tests proving the browser
  clients' wire contract against the real API: status shape, open returns
  PREPARING (no question, no plan leak), full lifecycle
  (open → begin → question → answers → ticket → finish → feedback →
  transcript), snapshot-as-resume, real privacy flags, ownership isolation,
  anonymous rejection, and honest unconfigured status. Uses a deterministic
  stub provider (the repo's established seam) — this proves the contract, not
  that a real model ran.

## Verified results

- `python -m pytest -n0 --no-cov -q` → **551 passed, 46 skipped, 0 failed.**
  (543 pre-existing + 8 new.)
- Both client pages: 0 unclosed tags, 0 tag mismatches, all inline scripts and
  the Tailwind config parse cleanly (`node new Function` check).
- Live HTTP boot: interview-room.html, interview-results.html, job-detail.html
  all serve 200; room client references `/api/interview/*`; results client reads
  `/feedback` and `/transcript`.

## Verified results (this pass)

The interview subsystem is now **wired into the app and green in the standard
test suite** (it was previously untracked and excluded via `collect_ignore`):

- `api/main.py` includes `interview` and `interview_mock` routers (canonical
  `/api/interview/*` + versioned `/api/v1/interview/*` aliases).
- `/api/version` build-identity endpoint added (public, secret-free, derived
  from the live app + OpenAPI schema); `/api/v1/version` mirrors it.
- `Settings` gained the interview configuration block (`INTERVIEW_ENABLED`,
  `LLM_*`, `INTERVIEW_*`, `WORKER_ENABLED`) plus the `interview_configured` /
  `interview_unavailable_reason` / `llm_endpoint` accessors the provider reads.
- `DiscoveryAgent._match_skill_watches` added and wired into the discovery
  cycle so a saved skill watch fires the moment a matching listing arrives
  (best-effort; a broken alert path is logged, never fatal to the cycle).
- Interview test files removed from `collect_ignore`; full suite:
  **557 passed, 27 skipped, 0 failed** (baseline was 301; +256 interview tests).
- Live smoke: `/api/interview/status` returns `live:false` with an honest
  reason and `audio_recorded:false` when no model is configured; the room
  client renders that unavailable state rather than a fake interview.

## External dependencies (not available in this sandbox)

- **A configured interview model.** `INTERVIEW_ENABLED=true` plus
  `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` pointing at an OpenAI-compatible
  endpoint. In this environment no usable free model was reachable
  (Ollama at :11434 had no loaded free model; the cloud model required credits;
  others timed out). So the **live conversation** could not be exercised here.
  The backend refuses cleanly when unconfigured (`/status` → `live:false`) and the
  room client shows that state honestly — it does not pretend to interview.
- **Speech** is browser-native (`SpeechRecognition`/`speechSynthesis`). There is
  deliberately no server-side STT/TTS/media store, which is why the privacy
  contract reports `audio_recorded: false`, `audio_transmitted: false`. This is
  the deployment's real capability, not a stub.

## Known limitations

- Coding workspace / screen-share exercise (spec §8) is **not** implemented —
  the backend interview domain has no code-execution or screen-capture surface,
  and building one would mean claiming a capability that does not exist. The
  room client therefore offers no such controls.
- The room client reads the bearer token from `localStorage`/`sessionStorage`
  under `dedan_token`. The current static pages have no sign-in flow that sets
  it, so from a purely static page the room shows "sign in to start a live
  interview". Wiring it to the app's real auth (`/api/auth/login` → store token)
  is a small follow-up.
- Multilingual interviews (spec §7): the client hard-codes `en-US` for
  recognition and speaks in English. The architecture is language-agnostic but
  no non-English path has been tested, so none is claimed.

## How to run it for real

```bash
export INTERVIEW_ENABLED=true
export LLM_BASE_URL=http://localhost:11434/v1   # any OpenAI-compatible endpoint
export LLM_MODEL=qwen3-coder:30b                 # a model that is actually pulled
uvicorn api.main:app --host 0.0.0.0 --port 8000  # serve the API + frontend
```

Sign in, store the token as `dedan_token` in localStorage, open a job, press
"Start AI interview".
