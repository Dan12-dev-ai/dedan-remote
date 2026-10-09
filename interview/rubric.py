"""
Interview rubric — what is measured, how it is measured, and how it is weighted.

Two very different things live here, on purpose:

1. **Model judgement** — the LLM reads the transcript and scores each
   dimension from the text, returning evidence for every number.
2. **Speech measurement** — words, pace, filler rate, sentence length and
   turn-to-turn consistency are *computed* from the transcript and the
   recorded answer durations. These are measurements, not opinions, so the
   report can show them verbatim.

Communication is the only dimension with both: half model read, half
measurement. That split is stated in the report the user sees.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# ── Dimensions ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Dimension:
    key: str
    label: str
    weight: float
    brief: str


DIMENSIONS: tuple[Dimension, ...] = (
    Dimension(
        "role_depth",
        "Role depth",
        0.26,
        "Command of the specific stack, tools and trade-offs this posting names. "
        "Depth of real ownership, not familiarity.",
    ),
    Dimension(
        "problem_solving",
        "Problem solving",
        0.20,
        "How the candidate frames an ambiguous problem, narrows it, and reasons "
        "to a decision under constraints.",
    ),
    Dimension(
        "critical_thinking",
        "Critical thinking",
        0.18,
        "Whether the candidate challenges premises, weighs alternatives, states "
        "uncertainty honestly and catches their own errors.",
    ),
    Dimension(
        "communication",
        "Communication",
        0.16,
        "Clarity, structure and listenability of the spoken answer.",
    ),
    Dimension(
        "creativity",
        "Creativity",
        0.10,
        "Originality of approach — non-obvious options, first-principles "
        "reframing, novel use of existing tools.",
    ),
    Dimension(
        "ownership",
        "Ownership",
        0.10,
        "Agency and follow-through: what they owned, what they delegated, what "
        "they would do differently.",
    ),
)

DIMENSION_KEYS: tuple[str, ...] = tuple(d.key for d in DIMENSIONS)
DIMENSION_BY_KEY: dict[str, Dimension] = {d.key: d for d in DIMENSIONS}

# Weights are used verbatim for the overall score; keep this honest.
assert abs(sum(d.weight for d in DIMENSIONS) - 1.0) < 1e-6, "dimension weights must sum to 1.0"


# ── Role bands (the bar this role is measured against) ─────────────────────


# Keyed by the experience level the listing implies. The value is the overall
# score expected of a *competent* candidate at that level; it is a fixed,
# published rubric constant, not a fitted percentile.
ROLE_BANDS: dict[str, tuple[float, str]] = {
    "senior": (78.0, "Senior / staff bar"),
    "advanced": (68.0, "Senior bar"),
    "intermediate": (58.0, "Mid-level bar"),
    "beginner": (48.0, "Entry bar"),
    "unknown": (55.0, "Unspecified bar"),
}

_STRONG_MARGIN = 10.0
_BORDERLINE_MARGIN = 8.0


def band_for(level: str | None) -> tuple[float, str]:
    key = (level or "").strip().lower()
    if key in ROLE_BANDS:
        return ROLE_BANDS[key]
    # Map the intelligence vocabulary onto the band keys.
    if key in ("lead", "principal", "expert"):
        return ROLE_BANDS["senior"]
    if key in ("mid", "mid-level"):
        return ROLE_BANDS["intermediate"]
    if key in ("junior", "entry", "graduate", "intern"):
        return ROLE_BANDS["beginner"]
    return ROLE_BANDS["unknown"]


def verdict_for(overall: float, bar: float) -> str:
    if overall >= bar + _STRONG_MARGIN:
        return "strong"
    if overall >= bar:
        return "competitive"
    if overall >= bar - _BORDERLINE_MARGIN:
        return "borderline"
    return "below_bar"


VERDICT_WORD: dict[str, str] = {
    "strong": "Strong",
    "competitive": "Competitive",
    "borderline": "Borderline",
    "below_bar": "Below bar",
}


# ── Speech measurement ──────────────────────────────────────────────────────

_FILLERS = (
    "um",
    "uh",
    "erm",
    "hmm",
    "mm",
    "like",
    "basically",
    "actually",
    "literally",
    "sort of",
    "kind of",
    "you know",
    "i mean",
)

_STRUCTURE_CUES = (
    "because",
    "so that",
    "which means",
    "the tradeoff",
    "the trade-off",
    "as a result",
    "the reason",
    "first",
    "then",
    "finally",
    "instead",
    "if i had",
    "next i",
    "the constraint",
    "the risk",
    "i'd measure",
    "i would measure",
)

_SENTENCE_SPLIT = re.compile(r"[.!?]+\s+")
_WORD = re.compile(r"[A-Za-z0-9'\-_]+")


def _tokens(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def _filler_count(words: list[str]) -> int:
    """Count filler occurrences, including two-word fillers."""
    text = " ".join(words)
    hits = 0
    for phrase in _FILLERS:
        if " " in phrase:
            hits += text.count(f" {phrase} ")
        else:
            hits += sum(1 for w in words if w == phrase)
    return hits


def _structure_count(words: list[str]) -> int:
    joined = " ".join(words)
    return sum(1 for cue in _STRUCTURE_CUES if cue in joined)


@dataclass
class SpeechMeasurement:
    """Everything about *how* the answer was delivered that can be counted."""

    turns: int = 0
    words: int = 0
    speaking_seconds: float = 0.0
    words_per_minute: float = 0.0
    pace_consistency: float = 0.0
    pace_spread_wpm: float = 0.0
    measured_turns: int = 0
    filler_rate: float = 0.0
    mean_sentence_words: float = 0.0
    longest_turn_seconds: float = 0.0
    monologue_share: float = 0.0
    questions_asked_back: int = 0
    structure_markers: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "turns": self.turns,
            "words": self.words,
            "speaking_seconds": round(self.speaking_seconds, 1),
            "words_per_minute": round(self.words_per_minute, 1),
            "pace_consistency": round(self.pace_consistency, 2),
            "pace_spread_wpm": round(self.pace_spread_wpm, 1),
            "measured_turns": self.measured_turns,
            "filler_rate": round(self.filler_rate, 3),
            "mean_sentence_words": round(self.mean_sentence_words, 1),
            "longest_turn_seconds": round(self.longest_turn_seconds, 1),
            "monologue_share": round(self.monologue_share, 3),
            "questions_asked_back": self.questions_asked_back,
            "structure_markers": self.structure_markers,
        }


def _stdev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return (sum((v - mean) ** 2 for v in values) / (len(values) - 1)) ** 0.5


def _band_score(value: float, lo: float, hi: float, tolerance: float) -> float:
    """1.0 inside [lo, hi], decaying linearly to 0 at ±tolerance beyond it."""
    if lo <= value <= hi:
        return 1.0
    distance = (lo - value) if value < lo else (value - hi)
    if tolerance <= 0:
        return 0.0
    return max(0.0, 1.0 - distance / tolerance)


def measure_speech(turns: list[dict[str, Any]]) -> SpeechMeasurement:
    """
    Deterministic delivery metrics from `{text, duration_seconds}` turns.

    No model is involved. Anything reported here is arithmetic on the
    transcript and the stopwatch, which is why the UI can present it as a
    measurement rather than an opinion.
    """
    m = SpeechMeasurement()
    if not turns:
        return m

    per_turn_wpm: list[float] = []
    all_words: list[str] = []
    total_seconds = 0.0
    longest = 0.0
    sentences: list[int] = []

    for turn in turns:
        text = (turn.get("text") or "").strip()
        seconds = float(turn.get("duration_seconds") or 0.0)
        words = _tokens(text)
        all_words.extend(words)
        total_seconds += seconds
        longest = max(longest, seconds)
        if seconds > 3 and len(words) >= 20:
            per_turn_wpm.append(len(words) / (seconds / 60.0))
        parts = [p for p in _SENTENCE_SPLIT.split(text) if p.strip()]
        sentences.append(max(1, len(parts)))
        # Every question mark is the candidate talking back to the interviewer.
        m.questions_asked_back += text.count("?")

    m.turns = len(turns)
    m.words = len(all_words)
    m.speaking_seconds = total_seconds
    m.longest_turn_seconds = longest
    m.words_per_minute = (m.words / (total_seconds / 60.0)) if total_seconds > 5 else 0.0

    spread = _stdev(per_turn_wpm)
    # CV of 0.25 → 0; CV of 0 → 1.
    mean_wpm = sum(per_turn_wpm) / len(per_turn_wpm) if per_turn_wpm else 0.0
    cv = (spread / mean_wpm) if mean_wpm > 0 else 1.0
    m.pace_consistency = max(0.0, min(1.0, 1.0 - (cv / 0.25)))
    m.pace_spread_wpm = spread
    m.measured_turns = len(per_turn_wpm)

    m.filler_rate = (_filler_count(all_words) / m.words) if m.words else 0.0
    m.mean_sentence_words = (m.words / sum(sentences)) if sentences else 0.0
    m.monologue_share = (longest / total_seconds) if total_seconds > 0 else 0.0
    m.structure_markers = _structure_count(all_words)

    return m


def speech_score(m: SpeechMeasurement) -> tuple[float, list[str]]:
    """
    Communication score from measurement only (0-100), plus the notes that
    explain it. Returned alongside the model's own read of the same dimension
    so neither number stands alone.
    """
    if m.words < 20:
        return 0.0, ["Not enough speech was captured to measure delivery."]

    notes: list[str] = []
    parts: dict[str, float] = {}

    # Pace: conversational interview speech sits around 120-165 wpm.
    if m.words_per_minute > 0:
        parts["pace"] = _band_score(m.words_per_minute, 120, 165, tolerance=70)
        if m.words_per_minute > 190:
            notes.append(
                f"Pace ran fast at {m.words_per_minute:.0f} wpm — slower lands better "
                "on technical content."
            )
        elif m.words_per_minute < 95:
            notes.append(
                f"Pace was slow at {m.words_per_minute:.0f} wpm; pauses read as "
                "uncertainty more often than as thought."
            )

    # Filler rate: per 100 words. 0 is excellent, 6+ is heavy.
    per_hundred = m.filler_rate * 100
    parts["fluency"] = _band_score(per_hundred, 0.0, 2.5, tolerance=6.0)
    if per_hundred > 4:
        notes.append(
            f"{per_hundred:.1f} filler words per 100 — that is what a listener hears as hesitation."
        )

    # Sentence length: 12-26 words reads as structured speech.
    parts["structure"] = _band_score(m.mean_sentence_words, 12, 26, tolerance=18)
    if m.mean_sentence_words > 34:
        notes.append(
            f"Average sentence ran {m.mean_sentence_words:.0f} words — break long "
            "thoughts into claim, then evidence."
        )

    parts["consistency"] = m.pace_consistency
    if m.measured_turns:
        notes.append(
            f"Pace varied {m.pace_spread_wpm:.0f} wpm across {m.measured_turns} measured "
            f"turns (average {m.words_per_minute:.0f} wpm)."
        )

    if m.monologue_share > 0.55 and m.turns > 2:
        notes.append(
            f"One answer held {m.monologue_share:.0%} of all your speaking time — "
            "the interviewer had no opening."
        )
    if m.structure_markers >= 4:
        notes.append(
            f"{m.structure_markers} causal/structural markers detected — the answers have a shape."
        )
    if not notes:
        notes.append("Delivery measured clean: steady pace, low filler, clear sentences.")

    if not parts:
        return 0.0, notes

    score = 100.0 * sum(parts.values()) / len(parts)
    return round(score, 1), notes


def combine_communication(model_score: float, measured_score: float) -> float:
    """
    Communication = half model read, half measurement.

    Splitting it this way is deliberate: the model sees structure and
    clarity of content, the arithmetic sees pace and filler. The report tells
    the user this is a blend, and shows both halves.

    Returns the blended score. (This was annotated as a ``tuple[float, list[str]]``
    it never produced; every caller assigns the result to a single float.)
    """
    blended = 0.5 * max(0.0, min(100.0, model_score)) + 0.5 * max(0.0, min(100.0, measured_score))
    return round(blended, 1)


def weighted_overall(dimension_scores: dict[str, float]) -> float:
    total = 0.0
    for dim in DIMENSIONS:
        value = dimension_scores.get(dim.key)
        if value is None:
            continue
        total += max(0.0, min(100.0, float(value))) * dim.weight
    return round(total, 1)


def strengths_gaps(
    dimension_scores: dict[str, float],
) -> tuple[list[str], list[str]]:
    """Rank the dimensions so the report leads with the real signal."""
    ordered = sorted(
        ((k, float(v)) for k, v in dimension_scores.items() if v is not None),
        key=lambda kv: kv[1],
        reverse=True,
    )
    strengths = [
        f"{DIMENSION_BY_KEY[k].label} — {v:.0f}/100"
        for k, v in ordered
        if k in DIMENSION_BY_KEY and v >= 65
    ][:3]
    gaps = [
        f"{DIMENSION_BY_KEY[k].label} — {v:.0f}/100"
        for k, v in reversed(ordered)
        if k in DIMENSION_BY_KEY and v < 55
    ][:3]
    return strengths, gaps
