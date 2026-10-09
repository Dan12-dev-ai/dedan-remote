"""
Evaluator — score one answer against a 0–100 rubric.

Three dimensions, weighted, each scored 0–100 independently so a failure is
attributable rather than averaged into mush:

    relevance   did the answer address the question and the target skill?
    accuracy    was the technical content correct?  (empty when not a tech probe)
    clarity     was it structured, specific and communicable?

Two design rules, both load-bearing:

- **Accuracy is not fabricated for behavioural questions.** "Was your STAR
  story technically accurate?" is not a meaningful question, so the dimension
  is reported as ``None`` and excluded from the weighted mean. Inventing a
  number there would make the radar chart look rigorous while measuring nothing.
- **A model that fails to score is a failure, not a default.** If the evaluator
  cannot produce a valid rubric, that answer is left unevaluated and the
  pipeline says so. It is never silently scored 50.

`evaluate_answer` is synchronous and single-answer; `interview/workers.py` runs
it off the request path so the candidate is never waiting on a rubric.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from interview.provider import LLMClient, ProviderError, ProviderUnavailable
from interview.skills import skill_label

logger = logging.getLogger("dedan.interview.evaluator")

# Weights applied to the *present* dimensions. Behavioural answers drop
# `accuracy` and are renormalised across what remains.
WEIGHTS = {"relevance": 0.4, "accuracy": 0.35, "clarity": 0.25}

#: Pseudo-skill attached to behavioural questions. It is a question *type*, not a
#: competency, so it gets its own radar axis rather than masquerading as a skill.
BEHAVIOURAL_AXIS = "transferable"

EVALUATOR_SYSTEM = (
    "You are the assessment panel for one interview answer. You are marking "
    "against a fixed rubric, not forming an opinion of the person.\n"
    "\n"
    "Hard rules:\n"
    "1. Judge only what is in the answer. Never award credit for confidence, "
    "fluency, seniority-sounding words, or claims the answer does not support "
    "with specifics.\n"
    "2. Every score must be justifiable from the answer text. If the answer is "
    "thin, empty or off-topic, score low and say exactly what was missing.\n"
    "3. An answer cut off by the timer is scored on what was said, and the "
    "feedback must note the truncation.\n"
    "4. Do not comment on personality, accent, gender, age or origin.\n"
    "5. 'accuracy' applies to technical content only. For a behavioural or "
    "system question, return null for accuracy — do not guess a number.\n"
    "Return JSON only."
)


class EvaluationError(RuntimeError):
    """The rubric could not be produced. The answer stays unevaluated."""


@dataclass
class AnswerEvaluation:
    score: float
    relevance_score: float
    clarity_score: float
    accuracy_score: Optional[float]
    detailed_feedback: str
    strengths: list[str] = field(default_factory=list)
    weak_areas: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    model: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "relevance_score": self.relevance_score,
            "clarity_score": self.clarity_score,
            "accuracy_score": self.accuracy_score,
            "detailed_feedback": self.detailed_feedback,
            "strengths": self.strengths,
            "weak_areas": self.weak_areas,
            "recommendations": self.recommendations,
            "model": self.model,
        }


def _clamp(value: Any, *, low: float = 0.0, high: float = 100.0) -> Optional[float]:
    """Coerce to a number inside range; anything else becomes ``None``.

    A model that returns ``"85%"`` or ``92`` as a string still scores, because
    silently dropping the answer over a formatting quirk is its own failure.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        digits = "".join(ch for ch in value if ch.isdigit() or ch == ".")
        if not digits:
            return None
        try:
            value = float(digits.rstrip("."))
        except ValueError:
            return None
    if not isinstance(value, (int, float)):
        return None
    return float(min(high, max(low, float(value))))


def _strings(value: Any, limit: int = 4) -> list[str]:
    """
    Coerce a model's list-of-strings field into real strings.

    A model that emits ``[null, "good point"]`` must not turn the ``null`` into
    the literal word "None" and present it to a candidate as feedback.
    """
    if isinstance(value, str):
        candidates: list[Any] = [value]
    elif isinstance(value, (list, tuple)):
        candidates = list(value)
    else:
        return []
    parts: list[str] = []
    for item in candidates:
        if item is None or isinstance(item, bool):
            continue
        if isinstance(item, (int, float)):
            continue
        text = item if isinstance(item, str) else str(item)
        text = text.strip()
        if text:
            parts.append(text)
        if len(parts) >= limit:
            break
    return parts


def compute_total(relevance: float, clarity: float, accuracy: Optional[float]) -> float:
    """Weighted mean over the dimensions actually present."""
    present = {"relevance": relevance, "clarity": clarity}
    if accuracy is not None:
        present["accuracy"] = accuracy
    weight = sum(WEIGHTS[k] for k in present)
    if weight == 0:
        return 0.0
    return round(sum(present[k] * WEIGHTS[k] for k in present) / weight, 1)


def evaluate_answer(
    *,
    question: dict[str, Any],
    response_text: str,
    job: Optional[dict[str, Any]] = None,
    client: Optional[LLMClient] = None,
) -> AnswerEvaluation:
    """
    Score one answer. Raises `EvaluationError` rather than returning a guess.
    """
    if client is None or not client.available:
        raise EvaluationError("no evaluator model configured")

    text = (response_text or "").strip()
    if not text:
        # An empty answer is a real outcome, scored deterministically: it
        # demonstrated nothing. Handled here so the model is never asked to
        # find nuance in a blank page.
        return AnswerEvaluation(
            score=0.0,
            relevance_score=0.0,
            clarity_score=0.0,
            accuracy_score=None,
            detailed_feedback=(
                "No answer was given, so nothing could be assessed. This is a "
                "zero rather than an estimate."
            ),
            strengths=[],
            weak_areas=["no response submitted"],
            recommendations=[
                "Answer even briefly — a partial answer with specifics beats silence."
            ],
            model=None,
        )

    skill = question.get("target_skill") or "general"
    label = skill_label(skill) if skill != "general" else "the role"
    timed_out = bool(question.get("timed_out"))

    payload = client.complete_json(
        system=EVALUATOR_SYSTEM,
        user=(
            "Score this one answer.\n\n"
            f"Question ({question.get('kind', 'unknown')} — {skill}):\n"
            f"{question['question_text']}\n\n"
            f"Target skill: {label}\n"
            f"Rubric focus: {', '.join(question.get('rubric_focus') or []) or 'general'}\n"
            f"Role context: {job.get('title') if job else 'unspecified'}\n"
            f"Timer expired before the answer finished: {timed_out}\n"
            f"Answer length: {len(text.split())} words\n\n"
            "CANDIDATE ANSWER:\n---\n"
            f"{text[:6000]}\n---\n\n"
            "Return JSON shaped exactly like:\n"
            '{"relevance": 0-100, "clarity": 0-100, "accuracy": 0-100 or null, '
            '"feedback": "...", "strengths": ["..."], "weak_areas": ["..."], '
            '"recommendations": ["..."]}'
        ),
        temperature=0.2,
        max_tokens=900,
    )

    relevance = _clamp(payload.get("relevance"))
    clarity = _clamp(payload.get("clarity"))
    accuracy = _clamp(payload.get("accuracy"))
    feedback = str(payload.get("feedback") or "").strip()

    # A rubric missing its two core dimensions is not a rubric. Refusing here is
    # what stops a malformed model reply from becoming a confident-looking 50.
    if relevance is None and clarity is None:
        raise EvaluationError("evaluator returned no usable scores")
    if relevance is None:
        relevance = 0.0
    if clarity is None:
        clarity = 0.0
    if not feedback:
        raise EvaluationError("evaluator returned no written feedback")

    return AnswerEvaluation(
        score=compute_total(relevance, clarity, accuracy),
        relevance_score=relevance,
        clarity_score=clarity,
        # Behavioural and system answers have no meaningful technical accuracy.
        accuracy_score=accuracy if question.get("kind") == "tech" else None,
        detailed_feedback=feedback,
        strengths=_strings(payload.get("strengths")),
        weak_areas=_strings(payload.get("weak_areas")),
        recommendations=_strings(payload.get("recommendations")),
        model=getattr(client._settings, "LLM_MODEL", None),  # noqa: SLF001
    )


# ── interview-level rollup ──────────────────────────────────────────────────


def build_radar(evaluations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Per-skill coverage for the radar chart.

    One axis per target skill, valued by that skill's mean overall score. A skill
    that was asked but never answered scores 0 rather than being dropped —
    dropping it would hide the gap the chart exists to show.
    """
    buckets: dict[str, list[Optional[float]]] = {}
    for row in evaluations:
        skill = row.get("target_skill") or "general"
        score = row.get("score")
        buckets.setdefault(skill, []).append(score if isinstance(score, (int, float)) else None)

    labels = {BEHAVIOURAL_AXIS: "Behavioural", "general": "General"}
    radar: list[dict[str, Any]] = []
    for skill, scores in buckets.items():
        present = [s for s in scores if s is not None]
        mean = round(sum(present) / len(present), 1) if present else 0.0
        radar.append(
            {
                "skill": skill,
                "label": labels.get(skill) or skill_label(skill),
                "score": mean,
                "asked": len(scores),
                "answered": len(present),
                "coverage": round(len(present) / len(scores), 2) if scores else 0.0,
                # Lets the UI style behavioural and technical axes differently.
                "axis": "behavioural" if skill == BEHAVIOURAL_AXIS else "skill",
            }
        )
    radar.sort(key=lambda r: (r["axis"] != "skill", -r["score"], r["skill"]))
    return radar


def summarise_interview(
    evaluations: list[dict[str, Any]],
    *,
    overall: Optional[str] = None,
) -> dict[str, Any]:
    """
    Roll per-answer evaluations into an interview-level verdict.

    ``coverage`` is reported alongside the score because a high mean over three
    answered questions out of eight is not a strong interview, and the report
    must not imply otherwise.
    """
    scored = [row for row in evaluations if isinstance(row.get("score"), (int, float))]
    total = round(sum(row["score"] for row in scored) / len(scored), 1) if scored else None
    radar = build_radar(evaluations)

    def rank(key: str) -> list[str]:
        """Most-mentioned first, de-duplicated, capped."""
        seen: list[str] = []
        for row in scored:
            for item in _strings(row.get(key), limit=3):
                if item not in seen:
                    seen.append(item)
        return seen[:6]

    dimension_totals: dict[str, list[float]] = {"relevance": [], "clarity": [], "accuracy": []}
    for row in scored:
        for key in dimension_totals:
            value = row.get(f"{key}_score")
            if isinstance(value, (int, float)):
                dimension_totals[key].append(float(value))

    return {
        "total_score": total,
        "answers_scored": len(scored),
        "answers_expected": len(evaluations),
        "coverage": round(len(scored) / len(evaluations), 2) if evaluations else 0.0,
        "overall_feedback": overall or "No overall commentary was produced for this interview.",
        "dimensions": {
            key: (round(sum(values) / len(values), 1) if values else None)
            for key, values in dimension_totals.items()
        },
        "strengths": rank("strengths"),
        "weak_areas": rank("weak_areas"),
        "recommendations": rank("recommendations"),
        "skill_radar": radar,
    }
