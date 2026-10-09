# Interview API — as built

**Status: implemented.** This document describes what the service actually
does today, not a proposal.

- Engine: `interview/engine.py`, `interview/rubric.py`
- Provider adapter: `interview/provider.py`
- Session store: `interview/store.py` (in-memory, TTL-bounded)
- HTTP surface: `api/routers/interview.py`, mirrored under `/api/v1`

---

## 1. Configuration

The live interview is **off unless a model is configured**. There is no
heuristic pretending to be one.

```bash
INTERVIEW_ENABLED=true
LLM_BASE_URL=https://api.openai.com/v1   # any OpenAI-compatible endpoint
LLM_API_KEY=sk-...                        # omit for local Ollama / llama.cpp
LLM_MODEL=gpt-4o-mini
INTERVIEW_MAX_QUESTIONS=12
INTERVIEW_MIN_QUESTIONS=5
INTERVIEW_SESSION_TTL_SECONDS=7200
INTERVIEW_MAX_SESSIONS=50
INTERVIEW_ROOM_RETENTION_DAYS=90
INTERVIEW_DB_PATH=data/interview.db
WORKER_ENABLED=true
```

`LLM_BASE_URL` only has to serve `POST {base}/chat/completions`. That single
shape covers OpenAI, Groq, Together, OpenRouter, vLLM, llama.cpp and Ollama —
switching providers is an environment change, not a code change.

`settings.interview_configured` is `True` when the flag is on, a model is named,
and either a key exists or the base URL is local. When it is `False` every
session endpoint returns `503 interview_not_configured` and the UI falls back to
browser-only rehearsal.

---

## 2. Endpoints

All are public (rate-limited, not authenticated) and exist under both `/api/…`
and `/api/v1/…`.

| Method | Path | Purpose |
| ------ | ---- | ------- |
| `GET` | `/interview/status` | Is a live interview possible right now? |
| `POST` | `/interview/sessions` | Start; returns the **first question only** |
| `GET` | `/interview/sessions/{id}` | Current state, for resuming after a reload |
| `POST` | `/interview/sessions/{id}/answers` | Submit an answer; returns the next question |
| `POST` | `/interview/sessions/{id}/finish` | Close it and return the ranked report |
| `DELETE` | `/interview/sessions/{id}` | Discard the transcript |

### `GET /interview/status`

Secret-free by construction — it reports capability, never the model name or any
provider detail.

```json
{ "live": true, "mode": "live", "detail": "" }
```

`detail` is a plain-language reason when `live` is `false`.

### `POST /interview/sessions`

```json
{ "slug": "retrieval-engineer-remote", "max_questions": 6 }
```

```json
{
  "session_id": "9f2c…",
  "status": "live",
  "role": {
    "title": "Retrieval Engineer (Remote)",
    "company": "Northwind",
    "category": "ai-ml",
    "experience": "advanced",
    "location": "Worldwide",
    "pay": "$120k - $150k",
    "tags": ["rag", "python"],
    "must_haves": ["…from the listing description…"],
    "responsibilities": ["…"],
    "signals": ["…from the ranking engine…"]
  },
  "total": 6,
  "answered": 0,
  "question": { "id": "q1", "kind": "opening", "prompt": "…", "seconds": 120 },
  "question_count": 6,
  "note": "Answers are evaluated as you go; scores appear in the final report only."
}
```

**The rest of the plan never leaves the server.** The plan is generated from the
stored row — title, description, tags, ranking signals — so the questions are
about this posting without the candidate being able to pre-read them.

### `POST /interview/sessions/{id}/answers`

```json
{ "text": "I owned the retrieval index end to end…", "duration_seconds": 61.7 }
```

```json
{ "turn": 1, "answered": 1, "total": 6, "question": { "id": "q2", … }, "complete": false }
```

**No score is returned here.** The answer is scored server-side at this moment;
the result is held until `/finish`. Grading mid-interview would turn the
session into a form that marks itself.

`duration_seconds` is a client-measured stopwatch for this answer. It feeds the
speech metrics; it is not trusted for anything else.

Answers under 8 words are recorded but not scored.

### `POST /interview/sessions/{id}/finish`

Returns the report described in §4.

### `DELETE /interview/sessions/{id}`

Best-effort. The transcript is dropped; if the call fails, the TTL still
expires it.

---

## 3. What is measured

Two different things, kept apart on purpose.

### Model judgement

The model reads the transcript and scores six dimensions against **this** role,
returning evidence for every number. Weights sum to 1.0.

| Dimension | Weight | What it measures |
| --------- | ------ | ---------------- |
| `role_depth` | 0.26 | Command of the stack, tools and trade-offs the posting names |
| `problem_solving` | 0.20 | Framing an ambiguous problem and reasoning to a decision |
| `critical_thinking` | 0.18 | Challenging premises, weighing alternatives, honest uncertainty |
| `communication` | 0.16 | Clarity and structure of the spoken answer |
| `creativity` | 0.10 | Non-obvious options, first-principles reframing |
| `ownership` | 0.10 | Agency: what was owned, delegated, done differently |

The overall score is the weighted mean. The role's bar comes from the listing's
own experience level:

| Level | Bar |
| ----- | --- |
| senior / staff / principal | 78 |
| advanced / senior | 68 |
| intermediate / mid | 58 |
| beginner / junior / entry | 48 |
| unspecified | 55 |

Verdict: `≥ bar+10` **strong**, `≥ bar` **competitive**, `≥ bar−8`
**borderline**, otherwise **below bar**. These are fixed published constants —
a rubric, not a fitted percentile.

### Speech measurement

`interview/rubric.py` computes these with arithmetic on the transcript and the
stopwatch. No model is involved, which is why the report presents them as
measurements:

- words, speaking seconds, words per minute
- pace consistency and spread across turns (coefficient of variation)
- filler rate per 100 words
- mean sentence length
- longest answer and its share of total speaking time
- questions asked back at the interviewer
- causal/structural markers ("because", "the trade-off", "as a result", …)

`communication` is the only blended dimension: **50% model read, 50%
measurement.** The report shows both halves separately so the reader can see how
the number was built.

---

## 4. The report

```json
{
  "session_id": "9f2c…",
  "role": { "…": "as returned at start" },
  "generated_at": "2026-09-28T10:14:02Z",
  "measurement": {
    "overall": 71.0,
    "band": 68.0,
    "band_label": "Senior bar",
    "verdict": "competitive",
    "verdict_word": "Competitive",
    "dimensions_at_band": "5/6",
    "questions_asked": 6,
    "questions_answered": 6,
    "turns_evaluated": 6,
    "turns_unevaluated": 0,
    "engineered_by": "live model judgement + measured speech metrics"
  },
  "dimensions": [
    { "key": "role_depth", "label": "Role depth", "weight": 0.26, "score": 78.0, "brief": "…" }
  ],
  "communication_split": { "model_read": 72.0, "measured": 68.0 },
  "speech": { "words": 812, "words_per_minute": 132.4, "filler_rate": 0.014, "…": "…" },
  "speech_notes": ["Pace varied 14 wpm across 6 measured turns (average 132 wpm)."],
  "strengths": ["Role depth — 78/100"],
  "gaps": ["Creativity — 52/100"],
  "role_analysis": {
    "headline": "Strong on ownership, thin on evaluation tooling.",
    "role_fit": "…",
    "what_they_proved": ["…"],
    "what_they_did_not_show": ["…"],
    "evidence": ["verbatim quotes"],
    "moves_the_rank": ["…"],
    "interviewer_notes": ["…"],
    "cheat_sheet": ["…"]
  },
  "turns": [
    {
      "kind": "role_depth", "prompt": "…", "intent": "…", "answer": "…",
      "words": 96, "duration_seconds": 61.7,
      "summary": "…", "note": "…", "evidence": ["…"],
      "scores": { "role_depth": 78.0 }, "evaluated": true
    }
  ],
  "limitations": [
    "Scores are one model's assessment of one conversation, not a hiring decision."
  ]
}
```

`limitations` is never empty. It names unevaluated turns, a failed written
debrief, and the standing caveat that this is not a hiring decision.

---

## 5. Failure behaviour

| Situation | Behaviour |
| --------- | --------- |
| No model configured | `503 interview_not_configured` on every session endpoint; `/status` returns `live: false` with a reason |
| Provider unreachable / non-2xx | `502 interview_model_failed`; the message never contains the API key |
| Evaluation of one answer fails | That turn is stored unevaluated, the interview continues, and `limitations` says how many turns were excluded |
| Written debrief fails | `role_analysis` comes back empty; `limitations` says why. Scores are still returned |
| Malformed model JSON | Parsed out of fences/prose; a genuinely unparseable reply raises `interview_model_failed` rather than being guessed at |
| Session expired or unknown | `404 interview_session_missing` |

Sessions are **in-memory only** — bounded by count and TTL, never written to
disk. A restart clears them, which is the honest outcome for a transcript.

---

## 6. Voice

Voice is the browser's own, not a vendor:

- **Out** — `speechSynthesis` reads each question. Feature-detected, mutable,
  with a watchdog so a dropped utterance cannot freeze the turn.
- **In** — `SpeechRecognition` / `webkitSpeechRecognition` dictates the answer.
  Interim text is shown but never committed; only final segments are.

Neither is required. With neither available the page types and reads nothing.
No audio is recorded, stored or uploaded by this app.

---

## 7. Rate limits

Per client IP, in `api/routers/interview.py`:

| Bucket | Limit |
| ------ | ----- |
| `interview_status` | 60 / min |
| `interview_start` | 6 / hour |
| `interview_answer` | 90 / hour |
| `interview_finish` | 6 / hour |
| `interview_cancel` | 6 / hour |
| `interview_get` | 120 / min |

Sessions are anonymous-capable because sign-in is not required to try the
product. Tighten `interview_start` before exposing this publicly — each start
costs several model calls.

---

## 8. The rehearsal fallback is a different thing

The rehearsal fallback is a local coverage heuristic that checks whether an answer mentions the signals the question asked about, whether it is long enough to contain an answer, and whether it has structure. It is deterministic, explainable, uploads nothing, and involves no model.

It is not a degraded live interview, and the UI does not present it as one.
Keep the two paths separate when changing either.