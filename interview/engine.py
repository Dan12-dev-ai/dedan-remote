"""
Interview engine — plans a role-specific interview, evaluates answers silently,
and produces one ranked report.

Flow, and what the client is allowed to see at each step:

    start()   -> first question + how many are coming (NOT the plan)
    submit()  -> silent evaluation + the next question (NO per-answer score)
    finish()  -> the full ranked report

Keeping the plan server-side and withholding per-answer scores is the whole
point: the candidate is talking to an interviewer, not filling in a form that
marks itself. Evaluation happens on submit; the result surfaces only in the
report.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from interview import rubric
from interview.provider import LLMClient, ProviderError, ProviderUnavailable, get_llm
from interview.store import InterviewStore, get_store
from utils.logger import get_logger

logger = get_logger(__name__)
llm_logger = logging.getLogger("dedan.interview.llm")

QUESTION_KINDS = ("opening", "background", "role_depth", "problem_solving", "scenario", "closing")

INTERVIEWER_SYSTEM = (
    "You are a senior hiring manager conducting a live, spoken technical interview. "
    "You are also a rigorous assessor. Two rules govern everything you say:\n"
    "1. Judge only what the candidate actually said. Never award credit for "
    "confidence, seniority-sounding words, or plausible-sounding claims that are "
    "not supported by specifics in the transcript.\n"
    "2. Every score you return must cite the evidence in the transcript that "
    "produced it. If the transcript is thin or off-topic, score low and say why.\n"
    "Write like an interviewer talking out loud: short, direct, one idea per "
    "sentence. No bullet lists, no markdown, no preamble."
)

REPORT_SYSTEM = (
    "You are a hiring manager writing the private interview debrief for this "
    "candidate, for THIS specific role. Rules:\n"
    "- Be specific to the posting. Do not give advice that would apply to any job.\n"
    "- Separate what the candidate demonstrated from what the role requires, and "
    "name the gap.\n"
    "- Cite transcript evidence for every claim. Quote sparingly and exactly.\n"
    "- No encouragement padding, no 'great answer'. If it was weak, say what was weak.\n"
    "- Do not invent anything the candidate did not say, and do not speculate about "
    "their character, gender, age, origin or accent.\n"
    "Return JSON only."
)


# ── Role profile ────────────────────────────────────────────────────────────


@dataclass
class RoleProfile:
    """Everything about the posting the interviewer is allowed to know."""

    slug: str
    title: str
    company: str
    category: str
    experience: str
    location: str
    pay: str
    tags: list[str] = field(default_factory=list)
    must_haves: list[str] = field(default_factory=list)
    responsibilities: list[str] = field(default_factory=list)
    signals: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "company": self.company,
            "category": self.category,
            "experience": self.experience,
            "location": self.location,
            "pay": self.pay,
            "tags": self.tags,
            "must_haves": self.must_haves,
            "responsibilities": self.responsibilities,
            "signals": self.signals,
        }

    def as_brief(self) -> str:
        lines = [
            f"Role: {self.title}",
            f"Company: {self.company}",
            f"Category: {self.category}",
            f"Level: {self.experience}",
            f"Location: {self.location}",
            f"Published pay: {self.pay}",
        ]
        if self.tags:
            lines.append(f"Tags on the listing: {', '.join(self.tags)}")
        if self.must_haves:
            lines.append("Named requirements: " + "; ".join(self.must_haves))
        if self.responsibilities:
            lines.append("Named responsibilities: " + "; ".join(self.responsibilities))
        if self.signals:
            lines.append("Signals from the DEDAN engine: " + "; ".join(self.signals))
        return "\n".join(lines)

    def role_vocabulary(self) -> list[str]:
        """
        The posting's own words, for measuring how role-specific an answer was.

        Derived, never curated by hand: if the candidate reaches for the listing's
        vocabulary they have read the posting or done the work, and that is a
        better specificity signal than any fixed word list. Used by the adaptive
        follow-up engine (`interview.followup`).
        """
        terms: list[str] = []
        seen: set[str] = set()
        for source in (self.tags, self.must_haves, self.signals, [self.title]):
            for raw in source or []:
                for word in re.findall(r"[\w+#.-]{3,}", str(raw)):
                    lowered = word.lower().strip(".-")
                    if len(lowered) < 3 or lowered in _STOPWORDS or lowered in seen:
                        continue
                    seen.add(lowered)
                    terms.append(lowered)
        return terms[:80]


_MUST_HAVE_PATTERNS = (
    r"required?",
    r"must have",
    r"you have",
    r"we are looking for",
    r"you should have",
    r"proficiency",
    r"experience with",
    r"knowledge of",
    r"familiar with",
)
_RESP_PATTERNS = (r"responsib", r"you will", r"the role involves", r"day[- ]to[- ]day", r"duties")

_STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "you",
    "your",
    "our",
    "are",
    "will",
    "can",
    "this",
    "that",
    "from",
    "work",
    "role",
    "team",
    "who",
    "what",
    "when",
    "how",
    "why",
    "remote",
    "job",
    "position",
    "years",
    "year",
    "experience",
    "strong",
    "good",
    "ability",
    "able",
    "have",
    "has",
    "using",
    "use",
    "across",
    "well",
    "their",
}


def _clean_list(values: list[str], limit: int = 6) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = re.sub(r"\s+", " ", (value or "").strip(" \t\r\n-*•"))
        if len(item) < 3 or len(item) > 160:
            continue
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
        if len(out) >= limit:
            break
    return out


def _sentences(text: str, patterns: tuple[str, ...], limit: int) -> list[str]:
    if not text:
        return []
    chunks = re.split(r"(?<=[.!?])\s+|\n+|[•*]\s*", text)
    hits: list[str] = []
    for chunk in chunks:
        low = chunk.lower()
        if any(re.search(p, low) for p in patterns):
            hits.append(chunk)
    return _clean_list(hits, limit)


def build_role_profile(job: dict[str, Any], ident: str) -> RoleProfile:
    """
    Derive the interview brief from the listing.

    Everything here comes from the stored row — title, description, tags and
    the engine's own signals. Nothing about the candidate is assumed.
    """
    description = job.get("description") or ""
    intelligence = job.get("intelligence") or {}
    tags = [str(t) for t in (job.get("tags") or [])][:8]

    must_haves = _sentences(description, _MUST_HAVE_PATTERNS, 6)
    responsibilities = _sentences(description, _RESP_PATTERNS, 5)
    if not must_haves and tags:
        must_haves = [t for t in tags if t.lower() not in _STOPWORDS][:5]

    signals: list[str] = []
    for insight in (intelligence.get("insights") or [])[:3]:
        if isinstance(insight, str):
            signals.append(insight)
    explanation = job.get("score_explanation") or {}
    for reason in (explanation.get("reasons") or [])[:3]:
        if isinstance(reason, dict):
            detail = reason.get("detail") or reason.get("label")
            if detail:
                signals.append(str(detail))

    experience = job.get("experience_hint") or intelligence.get("experience_level") or "Not stated"
    salary = job.get("salary")
    pay = str(salary) if salary and job.get("salary_disclosed") else "Not disclosed"

    return RoleProfile(
        slug=str(job.get("slug") or ident),
        title=str(job.get("title") or "this role"),
        company=str(job.get("company") or "the company"),
        category=str(job.get("category") or "not categorised"),
        experience=str(experience),
        location=str(job.get("location_label") or "Not stated"),
        pay=pay,
        tags=tags,
        must_haves=must_haves,
        responsibilities=responsibilities,
        signals=_clean_list(signals, 4),
    )


# ── Question plan ───────────────────────────────────────────────────────────

PLAN_SCHEMA_HINT = """Return JSON only, in exactly this shape:
{
  "questions": [
    {
      "kind": "opening|background|role_depth|problem_solving|scenario|closing",
      "prompt": "the spoken question, one or two sentences, addressed to the candidate",
      "intent": "one short line on what this question is probing",
      "targets": ["2-4 short keywords this question is designed to surface"]
    }
  ]
}"""


def plan_questions(
    profile: RoleProfile,
    count: int,
    client: Optional[LLMClient] = None,
) -> list[dict[str, Any]]:
    """
    Ask the model for a role-specific plan.

    Coverage is enforced here rather than trusted: the returned kinds are
    padded from a fixed ladder so a thin reply still produces a real
    interview. Question *text* is never invented locally.
    """
    client = client or get_llm()
    count = max(3, min(int(count), 10))
    asked = (
        f"Plan a {count}-question live interview for this role.\n\n"
        f"{profile.as_brief()}\n\n"
        "Rules:\n"
        f"- Exactly {count} questions.\n"
        "- Cover, in this order: an opening, evidence of relevant experience, one "
        "question that goes deep on a tool or requirement this posting actually "
        "names, one problem-solving or debugging question grounded in that "
        "requirement, one scenario with a real constraint, and a closing question "
        "that asks what they need to know.\n"
        "- Each question must be answerable in 60-120 spoken seconds and must be "
        "specific to this posting — not generic interview questions.\n"
        "- Never ask about protected characteristics.\n\n"
        f"{PLAN_SCHEMA_HINT}"
    )
    data = client.complete_json(
        system=INTERVIEWER_SYSTEM,
        user=asked,
        temperature=0.6,
        max_tokens=1600,
    )

    raw = data.get("questions")
    rows: list[dict[str, Any]] = []
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            prompt = str(item.get("prompt") or "").strip()
            if len(prompt) < 12:
                continue
            kind = str(item.get("kind") or "background").lower()
            if kind not in QUESTION_KINDS:
                kind = "background"
            targets = [str(t) for t in (item.get("targets") or [])][:4]
            rows.append(
                {
                    "kind": kind,
                    "prompt": prompt[:400],
                    "intent": str(item.get("intent") or "")[:200],
                    "targets": targets,
                }
            )

    ladder = ("opening", "background", "role_depth", "problem_solving", "scenario", "closing")
    seen: set[str] = set()
    for row in rows:
        seen.add(row["kind"])
    for kind in ladder:
        if len(rows) >= count:
            break
        if kind in seen:
            continue
        rows.append(
            {
                "kind": kind,
                "prompt": _fallback_prompt(kind, profile),
                "intent": f"{kind.replace('_', ' ')} coverage",
                "targets": [t for t in profile.tags[:2]],
            }
        )
        seen.add(kind)

    rows = rows[:count]
    for index, row in enumerate(rows):
        row["id"] = f"q{index + 1}"
        row["seconds"] = 120
    return rows


def _fallback_prompt(kind: str, profile: RoleProfile) -> str:
    """Only used to pad a thin plan; written from the listing, never invented."""
    anchor = (
        profile.tags[0]
        if profile.tags
        else (profile.must_haves[0] if profile.must_haves else "this work")
    )
    return {
        "opening": (
            f"Thanks for making time. Introduce yourself, then tell me why "
            f"{profile.company}'s {profile.title} role is worth your time."
        ),
        "background": (
            "Walk me through the work most relevant to this posting, and be precise "
            "about which parts you owned."
        ),
        "role_depth": (
            f"Take me deep on {anchor} — what you built, the constraint you were "
            "under, and the decision that mattered most."
        ),
        "problem_solving": (
            f"Something in production using {anchor} is failing intermittently and "
            "you own it. How do you find the cause?"
        ),
        "scenario": (
            "It is your first month and the real scope is larger than the posting "
            "described. What do you do in week one?"
        ),
        "closing": ("What would you need to see or ask before you say yes to this role?"),
    }.get(kind, "Tell me more about your relevant experience.")


# ── Per-answer evaluation (never returned to the candidate mid-interview) ───

TURN_SCHEMA_HINT = """Return JSON only, in exactly this shape:
{
  "summary": "one sentence: what this answer actually established",
  "evidence": ["up to 3 short verbatim quotes from the answer that justify the scores"],
  "scores": {
    "role_depth": 0,
    "problem_solving": 0,
    "critical_thinking": 0,
    "communication": 0,
    "creativity": 0,
    "ownership": 0
  },
  "note": "one sentence a hiring manager would say back to the candidate",
  "follow_up": "one short spoken sentence to bridge to the next question, or empty",
  "probed": true
}"""


def evaluate_turn(
    profile: RoleProfile,
    question: dict[str, Any],
    answer: str,
    speech: rubric.SpeechMeasurement,
    client: Optional[LLMClient] = None,
) -> dict[str, Any]:
    """Score one answer. Stored server-side; the client only sees the next line."""
    client = client or get_llm()
    measured_note = (
        f"Measured delivery for this answer so far: {speech.words} words, "
        f"{speech.words_per_minute:.0f} wpm, filler {speech.filler_rate * 100:.1f} per 100, "
        f"{speech.structure_markers} structure markers."
    )
    asked = (
        f"{profile.as_brief()}\n\n"
        f"QUESTION ASKED ({question.get('kind')}): {question.get('prompt')}\n"
        f"WHAT IT WAS PROBING: {question.get('intent') or 'unstated'}\n\n"
        f'CANDIDATE ANSWER:\n"""\n{answer}\n"""\n\n'
        f"{measured_note}\n\n"
        "Score every dimension 0-100 against this role. If the answer did not "
        "address the question, say so in the summary and score accordingly — do "
        "not reward effort. If the answer was too short to assess, return zeros.\n\n"
        f"{TURN_SCHEMA_HINT}"
    )
    data = client.complete_json(
        system=INTERVIEWER_SYSTEM,
        user=asked,
        temperature=0.25,
        max_tokens=1200,
    )

    raw_scores = data.get("scores") or {}
    scores: dict[str, float] = {}
    for key in rubric.DIMENSION_KEYS:
        value = raw_scores.get(key)
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        scores[key] = round(max(0.0, min(100.0, number)), 1)

    evidence = [str(e)[:220] for e in (data.get("evidence") or []) if str(e).strip()][:3]
    return {
        "summary": str(data.get("summary") or "")[:400],
        "evidence": evidence,
        "scores": scores,
        "note": str(data.get("note") or "")[:300],
        "follow_up": str(data.get("follow_up") or "")[:220],
        "probed": bool(data.get("probed", True)),
    }


# ── Session ─────────────────────────────────────────────────────────────────


@dataclass
class InterviewSession:
    id: str
    profile: RoleProfile
    plan: list[dict[str, Any]]
    created_at: datetime
    cursor: int = 0
    turns: list[dict[str, Any]] = field(default_factory=list)
    finished: bool = False
    report: Optional[dict[str, Any]] = None

    @property
    def total(self) -> int:
        return len(self.plan)

    def current_question(self) -> Optional[dict[str, Any]]:
        if self.cursor >= len(self.plan):
            return None
        return self.plan[self.cursor]

    def to_public(self) -> dict[str, Any]:
        question = self.current_question()
        return {
            "session_id": self.id,
            "status": "finished" if self.finished else "live",
            "role": self.profile.to_dict(),
            "total": self.total,
            "answered": len(self.turns),
            "question": (
                {
                    "id": question["id"],
                    "kind": question["kind"],
                    "prompt": question["prompt"],
                    "seconds": question.get("seconds", 120),
                }
                if question
                else None
            ),
        }


# ── Public operations ───────────────────────────────────────────────────────


class InterviewEngine:
    def __init__(
        self,
        client: Optional[LLMClient] = None,
        store: Optional[InterviewStore] = None,
    ) -> None:
        self._client = client
        self._store = store or get_store()

    @property
    def client(self) -> LLMClient:
        return self._client or get_llm()

    def available(self) -> tuple[bool, str]:
        try:
            self.client._require()  # noqa: SLF001 - capability probe
        except ProviderUnavailable as exc:
            return False, str(exc)
        return True, ""

    def get_session(self, session_id: str) -> InterviewSession:
        """
        Read a session from *this* engine's store.

        Callers must go through the engine rather than reaching for the store
        directly: an engine can be constructed with an injected store (tests,
        or a future per-tenant store), and mixing the two would read a
        different set of sessions than the engine writes.
        """
        return self._store.get(session_id)

    def discard(self, session_id: str) -> None:
        self._store.pop(session_id)

    def start(self, job: dict[str, Any], ident: str, count: int) -> InterviewSession:
        profile = build_role_profile(job, ident)
        plan = plan_questions(profile, count, client=self.client)
        session = InterviewSession(
            id=self._store.new_id(),
            profile=profile,
            plan=plan,
            created_at=datetime.now(timezone.utc),
        )
        self._store.put(session)
        return session

    def submit(self, session_id: str, text: str, duration_seconds: float) -> dict[str, Any]:
        session = self._store.get(session_id)
        question = session.current_question()
        if question is None:
            raise KeyError("interview already finished")

        answer = (text or "").strip()
        words = len(answer.split())
        turn: dict[str, Any] = {
            "question": question,
            "text": answer,
            "duration_seconds": max(0.0, float(duration_seconds or 0.0)),
            "words": words,
            "evaluation": None,
        }

        if words >= 8:
            speech = rubric.measure_speech(
                session.turns + [{"text": answer, "duration_seconds": turn["duration_seconds"]}]
            )
            try:
                turn["evaluation"] = evaluate_turn(
                    session.profile, question, answer, speech, client=self.client
                )
            except (ProviderError, ProviderUnavailable) as exc:
                # An evaluation failure must not destroy the interview. The turn
                # is kept unevaluated and the report says so explicitly.
                llm_logger.warning("turn evaluation failed: %s", exc)
                turn["evaluation"] = {"error": str(exc)}
        else:
            turn["evaluation"] = {"error": "answer too short to assess"}

        session.turns.append(turn)
        session.cursor += 1

        nxt = session.current_question()
        # Deliberately no scores here — evaluation is silent by design.
        return {
            "turn": len(session.turns),
            "answered": len(session.turns),
            "total": session.total,
            "question": (
                {
                    "id": nxt["id"],
                    "kind": nxt["kind"],
                    "prompt": nxt["prompt"],
                    "seconds": nxt.get("seconds", 120),
                }
                if nxt
                else None
            ),
            "complete": nxt is None,
        }

    def finish(self, session_id: str) -> dict[str, Any]:
        session = self._store.get(session_id)
        session.finished = True
        if session.report is None:
            session.report = build_report(session, client=self.client)
        return session.report

    def cancel(self, session_id: str) -> None:
        self._store.pop(session_id)


# ── Report ──────────────────────────────────────────────────────────────────


REPORT_SCHEMA_HINT = """Return JSON only, in exactly this shape:
{
  "headline": "one sentence verdict on this candidate for this role",
  "role_fit": "2-4 sentences on fit for THIS posting specifically",
  "evidence": ["up to 4 short verbatim quotes from the transcript"],
  "what_they_proved": ["3-5 short statements of demonstrated capability"],
  "what_they_did_not_show": ["2-4 short statements of what the role needs that is absent"],
  "moves_the_rank": ["3 concrete things that would raise their score on THIS role"],
  "interviewer_notes": ["2-3 short observations an interviewer would raise"],
  "cheat_sheet": ["up to 5 short, specific things to rehearse for this posting"]
}"""


def build_report(
    session: InterviewSession,
    client: Optional[LLMClient] = None,
) -> dict[str, Any]:
    """Aggregate scored turns into one ranked, role-specific report."""
    client = client or get_llm()
    profile = session.profile

    speech = rubric.measure_speech(session.turns)
    measured_speech, measured_notes = rubric.speech_score(speech)

    # Average the per-turn model scores across evaluated turns.
    averaged: dict[str, float] = {key: 0.0 for key in rubric.DIMENSION_KEYS}
    counted = 0
    unevaluated = 0
    for turn in session.turns:
        evaluation = turn.get("evaluation") or {}
        if evaluation.get("error"):
            unevaluated += 1
            continue
        scores = evaluation.get("scores") or {}
        if not scores:
            unevaluated += 1
            continue
        counted += 1
        for key in rubric.DIMENSION_KEYS:
            if key in scores:
                averaged[key] += float(scores[key])

    if counted:
        averaged = {k: round(v / counted, 1) for k, v in averaged.items()}
    else:
        averaged = {}

    model_communication = averaged.get("communication")
    communication_split = None
    if model_communication is not None:
        final_communication = rubric.combine_communication(model_communication, measured_speech)
        communication_split = {
            "model_read": round(model_communication, 1),
            "measured": round(measured_speech, 1),
        }
        averaged["communication"] = final_communication

    overall = rubric.weighted_overall(averaged) if averaged else 0.0
    bar, bar_label = rubric.band_for(profile.experience)
    verdict = rubric.verdict_for(overall, bar) if averaged else "borderline"
    strengths, gaps = rubric.strengths_gaps(averaged) if averaged else ([], [])

    at_bar = sum(1 for value in averaged.values() if value >= bar)

    transcript = _render_transcript(session)
    narrative: dict[str, Any] = {}
    narrative_error: Optional[str] = None
    if transcript.strip():
        try:
            narrative = client.complete_json(
                system=REPORT_SYSTEM,
                user=(
                    f"{profile.as_brief()}\n\n"
                    f"Measured delivery across the interview: {speech.words} words in "
                    f"{speech.speaking_seconds:.0f}s, {speech.words_per_minute:.0f} wpm, "
                    f"{speech.questions_asked_back} questions asked back, "
                    f"{speech.structure_markers} structure markers.\n\n"
                    f"FULL TRANSCRIPT:\n{transcript}\n\n"
                    f"Write the debrief for this role.\n\n{REPORT_SCHEMA_HINT}"
                ),
                temperature=0.3,
                max_tokens=1800,
            )
        except (ProviderError, ProviderUnavailable) as exc:
            narrative_error = str(exc)
            llm_logger.warning("report narrative failed: %s", exc)

    turns_report = [
        {
            "kind": turn["question"]["kind"],
            "prompt": turn["question"]["prompt"],
            "intent": turn["question"].get("intent", ""),
            "answer": turn["text"],
            "words": turn["words"],
            "duration_seconds": round(turn["duration_seconds"], 1),
            "summary": (turn.get("evaluation") or {}).get("summary", ""),
            "note": (turn.get("evaluation") or {}).get("note", ""),
            "evidence": (turn.get("evaluation") or {}).get("evidence", []),
            "scores": (turn.get("evaluation") or {}).get("scores", {}),
            "evaluated": not (turn.get("evaluation") or {}).get("error"),
        }
        for turn in session.turns
    ]

    return {
        "session_id": session.id,
        "role": profile.to_dict(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "measurement": {
            "overall": overall,
            "band": bar,
            "band_label": bar_label,
            "verdict": verdict,
            "verdict_word": rubric.VERDICT_WORD[verdict],
            "dimensions_at_band": f"{at_bar}/{len(averaged) if averaged else 0}",
            "questions_asked": session.total,
            "questions_answered": sum(1 for t in session.turns if t["words"] > 0),
            "turns_evaluated": counted,
            "turns_unevaluated": unevaluated,
            "engineered_by": "live model judgement + measured speech metrics",
        },
        "dimensions": [
            {
                "key": dim.key,
                "label": dim.label,
                "weight": dim.weight,
                "score": averaged.get(dim.key),
                "brief": dim.brief,
            }
            for dim in rubric.DIMENSIONS
        ],
        "communication_split": communication_split,
        "speech": speech.to_dict(),
        "speech_notes": measured_notes,
        "strengths": strengths,
        "gaps": gaps,
        "role_analysis": {
            "headline": str(narrative.get("headline") or ""),
            "role_fit": str(narrative.get("role_fit") or ""),
            "what_they_proved": [str(x)[:240] for x in (narrative.get("what_they_proved") or [])][
                :5
            ],
            "what_they_did_not_show": [
                str(x)[:240] for x in (narrative.get("what_they_did_not_show") or [])
            ][:4],
            "evidence": [str(x)[:220] for x in (narrative.get("evidence") or [])][:4],
            "moves_the_rank": [str(x)[:240] for x in (narrative.get("moves_the_rank") or [])][:3],
            "interviewer_notes": [str(x)[:240] for x in (narrative.get("interviewer_notes") or [])][
                :3
            ],
            "cheat_sheet": [str(x)[:200] for x in (narrative.get("cheat_sheet") or [])][:5],
        },
        "turns": turns_report,
        "limitations": _limitations(narrative_error, unevaluated, counted, session),
    }


def _limitations(
    narrative_error: Optional[str],
    unevaluated: int,
    counted: int,
    session: InterviewSession,
) -> list[str]:
    """Stated plainly, so the report never overstates itself."""
    notes: list[str] = []
    if unevaluated:
        notes.append(
            f"{unevaluated} of {len(session.turns)} answers could not be scored by the "
            "model and are excluded from the averages."
        )
    if counted == 0:
        notes.append("No answer cleared the length threshold, so no model score exists.")
    if not any(t["words"] > 0 for t in session.turns):
        notes.append("The interview recorded no spoken answers.")
    if narrative_error:
        notes.append(f"The written debrief could not be generated ({narrative_error}).")
    notes.append("Scores are one model's assessment of one conversation, not a hiring decision.")
    return notes


def _render_transcript(session: InterviewSession) -> str:
    lines: list[str] = []
    for index, turn in enumerate(session.turns, start=1):
        lines.append(f"Q{index} [{turn['question']['kind']}]: {turn['question']['prompt']}")
        lines.append(f"A{index}: {turn['text'] or '(no answer recorded)'}")
    return "\n\n".join(lines)
