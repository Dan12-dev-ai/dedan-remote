"""
Adaptive follow-ups — the thing that makes an interview a conversation.

A fixed question ladder is a form with voices. What separates an interview from
a scripted quiz is what the interviewer does *after* an answer:

    strong, specific answer   -> go deeper on the trade-off they just made
    correct but thin          -> probe the reasoning, not the conclusion
    vague or off-question     -> ask again, narrowed, not accusatory
    unassessable / skipped    -> move on and keep the door open

Everything here is **server-side**. The candidate receives the follow-up line
and nothing else — not the reason, not the depth band, not the model's
confidence. Surfacing those is how a rehearsal turns into a game.

Decision policy, stated so it can be tested:

- Depth bands are computed from the turn's own scores, never from a global
  running score. One strong answer in a weak interview should raise the *next*
  question, not rewrite the last one.
- A follow-up never consumes a slot from the plan. The plan is a floor, not a
  ceiling, and the total number of questions stays bounded by
  `FOLLOWUP_BUDGET_RATIO` so depth cannot run away into a 40-question session.
- Every follow-up is anchored to a named requirement from the posting. An
  interview that drifts off the listing is not adaptive, it is random.
- If the model is unavailable the decision is still made, deterministically, from
  the rubric scores we already have. Adaptivity that disappears when the
  provider hiccups is worse than none, because the candidate can hear it happen.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Optional, Sequence

from interview.provider import LLMClient, ProviderError, ProviderUnavailable

if TYPE_CHECKING:  # avoids importing the engine (which imports this module)
    from interview.engine import RoleProfile

logger = logging.getLogger("dedan.interview.llm")

#: At most this fraction of the plan may be spent on follow-ups. 0.4 means a
#: 6-question plan may grow to at most 8 exchanges.
FOLLOWUP_BUDGET_RATIO = 0.4

#: Cap in absolute terms, so a 10-question plan still terminates.
FOLLOWUP_BUDGET_CAP = 4

#: Below this the answer did not clear the specificity threshold for ADVANCED.
_SPECIFIC_STRONG = 0.22

#: Minimum words before a follow-up is even considered. Under this the
#: interviewer does not have anything to probe.
MIN_PROBE_WORDS = 12


class Depth(StrEnum):
    """How hard the next question should push."""

    #: The answer did not engage. Re-ask, narrower.
    FOUNDATIONAL = "foundational"
    #: Normal depth. The plan continues.
    STANDARD = "standard"
    #: Push on the trade-off the candidate just made.
    DEEPER = "deeper"
    #: An advanced counterfactual or cross-cutting trade-off.
    ADVANCED = "advanced"


class FollowUpAction(StrEnum):
    ASK_FOLLOW_UP = "ask_follow_up"
    ADVANCE = "advance"
    CLOSE = "close"


#: Score at or above which an answer is considered strong on the dimensions that
#: matter for depth decisions.
_STRONG = 72.0
#: At or above this, but not strong.
_SOLID = 55.0
#: Below this the answer missed the question rather than answering it shallowly.
_WEAK = 40.0

_FOLLOWUP_SYSTEM = (
    "You are a senior interviewer, mid-conversation. You have just heard one "
    "answer and you must decide what to say next.\n"
    "Hard rules:\n"
    "1. Ask exactly ONE question. Two questions at once is a lecture, not an interview.\n"
    "2. Build on what the candidate actually said. Quote or reference their own words "
    "where you can — it is the difference between a real interview and a script.\n"
    "3. Never reveal, repeat or hint at any score, rating, rubric or internal note. "
    "The candidate must not learn that they are being graded mid-answer.\n"
    "4. Never encourage, flatter, scold or threaten. Stay level and curious.\n"
    "5. Stay on this posting. A follow-up that would fit any job is a wasted minute.\n"
    "6. One or two sentences, spoken aloud. No markdown, no preamble, no 'great question'.\n"
    "Return JSON only."
)


@dataclass(frozen=True)
class FollowUpDecision:
    """
    What to do after one answer.

    ``reason`` is **internal**. It is written to the durable record for auditing
    and never included in an API response — see ``to_public``.
    """

    action: FollowUpAction
    depth: Depth
    #: The spoken line to the candidate. Empty when ``action`` is ``ADVANCE``.
    prompt: str = ""
    #: Server-side justification. Not for the candidate.
    reason: str = ""
    #: Whether this answer warranted a probe at all.
    probed: bool = False
    #: Bookkeeping so the budget is auditable.
    anchor_skill: str = ""
    spent: int = 0
    remaining: int = 0

    def to_public(self) -> dict[str, Any]:
        """
        The only shape allowed to reach a browser.

        Deliberately omits ``reason`` and the numeric depth: the candidate sees a
        question, not a scoring decision.
        """
        return {
            "action": self.action.value,
            "prompt": self.prompt,
            "probed": self.probed,
            "anchor_skill": self.anchor_skill,
        }

    def to_record(self) -> dict[str, Any]:
        """The full decision, for the transcript of record."""
        return {
            "action": self.action.value,
            "depth": self.depth.value,
            "prompt": self.prompt,
            "reason": self.reason,
            "probed": self.probed,
            "anchor_skill": self.anchor_skill,
            "spent": self.spent,
            "remaining": self.remaining,
        }


def decide_depth(
    scores: dict[str, float],
    *,
    answer: str,
    remaining_plan: int,
    role_terms: Sequence[str] = (),
) -> tuple[Depth, str]:
    """
    Map one answer's scores to a depth band and a human reason.

    Deterministic and pure. The two dimensions that decide depth are role depth
    and problem solving: a candidate can be eloquent and shallow, and pushing
    harder on an eloquent shallow answer produces a longer, still-shallow one.
    """
    words = len((answer or "").split())
    if words < MIN_PROBE_WORDS:
        return Depth.FOUNDATIONAL, f"only {words} words — not enough to probe"

    depth_scores = [
        float(scores.get(key, 0.0))
        for key in ("role_depth", "problem_solving", "critical_thinking")
        if key in scores
    ]
    if not depth_scores:
        return Depth.STANDARD, "no depth dimension was scored"

    mean = sum(depth_scores) / len(depth_scores)
    # Specificity is the tell. A high mean on short, generic prose is not depth.
    specificity = _specificity(answer, role_terms=role_terms)

    if mean >= _STRONG and specificity >= _SPECIFIC_STRONG:
        if remaining_plan <= 1:
            return Depth.DEEPER, "strong and specific, but little plan left — one deep probe"
        return Depth.ADVANCED, f"strong ({mean:.0f}) and specific ({specificity:.2f})"
    if mean >= _STRONG:
        return (
            Depth.DEEPER,
            f"strong ({mean:.0f}) but generic ({specificity:.2f}) — probe the reasoning",
        )
    if mean >= _SOLID:
        return Depth.DEEPER, f"solid ({mean:.0f}) — go one level deeper"
    if mean >= _WEAK:
        return Depth.STANDARD, f"partial ({mean:.0f}) — keep the current depth, narrow the next ask"
    return Depth.FOUNDATIONAL, f"weak ({mean:.0f}) — re-ask, narrowed"


#: Function words carry no signal in either direction, so they are excluded from
#: the specificity denominator. Measuring "did they say something concrete"
#: against a bag of `the`/`and`/`you` is a measurement of English, not of them.
_FILLER = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "you",
        "your",
        "our",
        "are",
        "was",
        "were",
        "will",
        "would",
        "can",
        "could",
        "should",
        "this",
        "that",
        "these",
        "those",
        "from",
        "have",
        "has",
        "had",
        "not",
        "but",
        "they",
        "them",
        "their",
        "there",
        "then",
        "than",
        "when",
        "what",
        "which",
        "while",
        "who",
        "how",
        "why",
        "all",
        "any",
        "some",
        "more",
        "most",
        "other",
        "into",
        "over",
        "under",
        "about",
        "because",
        "just",
        "also",
        "very",
        "really",
        "actually",
        "basically",
        "thing",
        "things",
        "stuff",
        "get",
        "got",
        "going",
        "go",
        "make",
        "made",
        "take",
        "took",
        "need",
        "want",
        "like",
        "know",
        "think",
        "said",
        "say",
        "says",
        "thing",
        "lot",
        "bit",
        "kind",
        "sort",
        "way",
        "ways",
        "time",
        "times",
        "one",
        "two",
        "first",
    }
)


#: Curated technical vocabulary. Kept deliberately broad rather than exhaustive:
#: a lexicon can always be beaten by a good answer, which is why proper nouns,
#: numerals and role-vocabulary overlap carry most of the weight.
_TECHNICAL = frozenset(
    {
        # distributed systems / infrastructure
        "cache",
        "caching",
        "index",
        "indexes",
        "indexing",
        "schema",
        "queue",
        "queues",
        "shard",
        "sharded",
        "sharding",
        "replica",
        "replicas",
        "partition",
        "partitions",
        "partitioned",
        "throughput",
        "latency",
        "failover",
        "rollback",
        "idempotent",
        "idempotency",
        "consistency",
        "consensus",
        "quorum",
        "rebalancing",
        "backpressure",
        "dead-letter",
        "checkpoint",
        "checkpointing",
        "topic",
        "topics",
        "consumer",
        "consumers",
        "producer",
        "producers",
        "broker",
        "brokers",
        "pipeline",
        "pipelines",
        "batch",
        "batching",
        "streaming",
        "stream",
        "retry",
        "retries",
        "dedupe",
        "deduplication",
        "canonical",
        "tier",
        "tiers",
        "replication",
        "drift",
        "migration",
        # AI / ML
        "token",
        "tokens",
        "tokenizer",
        "vector",
        "vectors",
        "embedding",
        "embeddings",
        "gradient",
        "gradients",
        "transformer",
        "attention",
        "inference",
        "training",
        "fine-tuning",
        "finetuning",
        "finetune",
        "dataset",
        "datasets",
        "corpus",
        "quantization",
        "distillation",
        "hallucination",
        "guardrail",
        "guardrails",
        "prompt",
        "prompts",
        "prompting",
        "overfitting",
        "backpropagation",
        "hyperparameter",
        "benchmark",
        "benchmarks",
        "evals",
        # software engineering
        "deploy",
        "deployment",
        "deployments",
        "observability",
        "monitoring",
        "alerting",
        "telemetry",
        "instrumentation",
        "tracing",
        "refactor",
        "refactoring",
        "codebase",
        "api",
        "apis",
        "endpoint",
        "endpoints",
        "contract",
        "contracts",
        "service",
        "services",
        "microservice",
        "microservices",
        "container",
        "containers",
        "kubernetes",
        "docker",
        "terraform",
        "nginx",
        "postgres",
        "postgresql",
        "redis",
        "kafka",
        "kinesis",
        "grpc",
        "graphql",
        "websocket",
        "concurrency",
        "parallelism",
        "thread",
        "threads",
        "coroutine",
        "mutex",
        "race",
        # data
        "warehouse",
        "etl",
        "aggregation",
        "aggregate",
        "aggregates",
        "join",
        "joins",
        "spark",
        "pandas",
        # measurement / operations
        "percentile",
        "baseline",
        "bottleneck",
        "profiling",
        "regression",
        "scaling",
        "scale",
        "scaled",
        "capacity",
        "quota",
        "budget",
        "cost",
        "costs",
        "overhead",
        "optimisation",
        "optimization",
        "optimize",
        "optimise",
        "amortized",
        "amortised",
        "testing",
    }
)


#: Connectives that signal explicit reasoning rather than assertion. A candidate
#: who says "I would cache, *because* the read path dominates at 80% of traffic"
#: is reasoning; one who says "I would cache" is not.
_REASONING_MARKERS = frozenset(
    {
        "because",
        "since",
        "therefore",
        "instead",
        "rather",
        "however",
        "although",
        "though",
        "whereas",
        "unless",
        "meanwhile",
        "consequently",
        "trade-off",
        "tradeoff",
        "trade",
        "constraint",
        "constraints",
        "bottleneck",
        "assumption",
        "assumptions",
        "risk",
        "risks",
        "degrade",
        "degradation",
        "fails",
        "failure",
        "failsafe",
        "fallback",
        "sacrifice",
        "give",
        "gave",
        "given",
        "cost",
        "costs",
    }
)


def _is_signal(token: str) -> bool:
    """Whether one token carries technical or evidential weight."""
    lowered = token.lower().strip(".,;:()")
    if not lowered:
        return False
    if lowered in _TECHNICAL or lowered in _REASONING_MARKERS:
        return True
    # Numerals are the strongest evidence of real work: "40 ms", "120k", "p99".
    if any(ch.isdigit() for ch in lowered):
        return True
    # Hyphenated compounds are usually named patterns, not adjectives.
    if "-" in lowered and len(lowered) > 4:
        return True
    # Capitalised mid-sentence words are named tools and systems (Kafka, Redis).
    return token[:1].isupper() and not token.isupper() and len(token) > 2


def _specificity(answer: str, *, role_terms: Sequence[str] = ()) -> float:
    """
    Ratio of *content* words that carry technical, evidential or role-specific signal.

    A crude, cheap proxy for "did they say anything specific" — computed with no
    model involved, so it still works when the provider is down. Three weights,
    because they are not equally trustworthy:

      * role vocabulary from the posting (weight 2) — if the candidate reaches
        for the listing's own words, they have read or done the work.
      * technical / evidential vocabulary and proper nouns (weight 1).
      * everything else (weight 0).

    Function words are excluded from the denominator; measuring specificity
    against a bag of `the`/`and`/`you` measures English, not the candidate.
    """
    words = [w for w in re_words(answer or "") if len(w) > 2 and w.lower() not in _FILLER]
    if not words:
        return 0.0
    role = {t.lower() for t in role_terms if len(t) > 2}
    weight = 0.0
    for word in words:
        if _is_signal(word):
            weight += 1.0
        elif role and any(word.lower() in term or term in word.lower() for term in role):
            weight += 2.0
    return round(min(1.0, weight / len(words)), 3)


def re_words(text: str) -> list[str]:
    """Word split. Isolated so tests can reason about tokenisation directly."""
    return re.findall(r"[\w'-]+", text or "")


# ── prompt construction ──────────────────────────────────────────────────────


FOLLOWUP_SCHEMA_HINT = """Return JSON only, in exactly this shape:
{
  "question": "the one follow-up question, one or two spoken sentences",
  "reason": "one short line for the interviewer, not for the candidate",
  "anchor_skill": "the requirement from the posting this probes"
}"""


def _depth_instruction(depth: Depth) -> str:
    return {
        Depth.FOUNDATIONAL: (
            "The answer did not engage with the question. Ask the same thing again, "
            "narrower and more concrete. Give one example of what a good answer "
            "would contain as a question, not as a hint."
        ),
        Depth.STANDARD: (
            "Stay at the current depth. Move the conversation forward with a question "
            "that tests the same competency from a different angle."
        ),
        Depth.DEEPER: (
            "Go one level deeper than the question you just asked. Force a trade-off: "
            "what did it cost, what would break it, what did you give up."
        ),
        Depth.ADVANCED: (
            "Go to the hard version. Introduce a constraint or a counterfactual that "
            "changes the obvious answer, and see whether the candidate can adapt."
        ),
    }[depth]


def build_follow_up_prompt(
    profile: "RoleProfile",
    question: dict[str, Any],
    answer: str,
    *,
    depth: Depth,
    turn_index: int,
    turn_count: int,
) -> str:
    """The prompt handed to the model for one follow-up line."""
    requirements = profile.must_haves or profile.tags or ["the role's core work"]
    return (
        f"{profile.as_brief()}\n\n"
        f"THIS IS TURN {turn_index} OF {turn_count} IN A LIVE INTERVIEW.\n\n"
        f"QUESTION YOU ASKED ({question.get('kind')}): {question.get('prompt')}\n"
        f"WHAT IT WAS PROBING: {question.get('intent') or 'unstated'}\n\n"
        f'WHAT THE CANDIDATE SAID:\n"""\n{(answer or "").strip()}\n"""\n\n'
        f"REQUIREMENTS NAMED BY THE POSTING: {'; '.join(requirements[:4])}\n"
        f"DIFFICULTY FOR THIS FOLLOW-UP: {depth.value}\n\n"
        f"{_depth_instruction(depth)}\n"
        "Ask about something the candidate has NOT already answered. If they gave a "
        "complete answer, push on the consequence or the cost. If they did not, ask a "
        "narrower version of the same thing.\n\n"
        f"{FOLLOWUP_SCHEMA_HINT}"
    )


@dataclass
class FollowUpEngine:
    """
    Generates follow-up lines, with a deterministic fallback.

    Injected with the client so tests can drive it without a provider.
    """

    client: Optional[LLMClient] = None
    #: When no model line can be produced, say so in the transcript rather than
    #: pretending the interviewer chose the question.
    degraded: bool = False
    _calls: list[str] = field(default_factory=list, repr=False)

    def budget_for(self, plan_total: int) -> int:
        return max(0, min(FOLLOWUP_BUDGET_CAP, int(plan_total * FOLLOWUP_BUDGET_RATIO)))

    def decide(
        self,
        *,
        profile: "RoleProfile",
        question: dict[str, Any],
        answer: str,
        scores: dict[str, float],
        remaining_plan: int,
        plan_total: int,
        followups_spent: int,
        turn_index: int,
        turn_count: int,
        last_question: bool,
    ) -> FollowUpDecision:
        """Choose the next move. Never raises — a decision is always produced."""
        budget = self.budget_for(plan_total)
        remaining = max(0, budget - followups_spent)

        depth, reason = decide_depth(
            scores,
            answer=answer,
            remaining_plan=remaining_plan,
            role_terms=profile.role_vocabulary(),
        )

        if last_question and depth in (Depth.FOUNDATIONAL, Depth.STANDARD):
            return FollowUpDecision(
                action=FollowUpAction.ADVANCE,
                depth=depth,
                reason="last planned question and nothing worth probing",
                probed=False,
                spent=followups_spent,
                remaining=remaining,
            )

        if remaining <= 0:
            self.degraded = True
            return FollowUpDecision(
                action=FollowUpAction.ADVANCE,
                depth=depth,
                reason=(f"{reason}; follow-up budget exhausted ({followups_spent}/{budget})"),
                probed=False,
                spent=followups_spent,
                remaining=0,
            )

        if depth is Depth.FOUNDATIONAL and remaining_plan <= 1 and last_question:
            return FollowUpDecision(
                action=FollowUpAction.ADVANCE,
                depth=depth,
                reason=f"{reason}; out of plan and the answer was not probeable",
                probed=False,
                spent=followups_spent,
                remaining=remaining,
            )

        prompt, anchor = self._line(
            profile=profile,
            question=question,
            answer=answer,
            depth=depth,
            turn_index=turn_index,
            turn_count=turn_count,
        )

        if not prompt:
            self.degraded = True
            return FollowUpDecision(
                action=FollowUpAction.ADVANCE,
                depth=depth,
                reason=f"{reason}; no follow-up line could be produced",
                probed=False,
                spent=followups_spent,
                remaining=remaining,
            )

        return FollowUpDecision(
            action=FollowUpAction.ASK_FOLLOW_UP,
            depth=depth,
            prompt=prompt,
            reason=reason,
            probed=True,
            anchor_skill=anchor,
            spent=followups_spent + 1,
            remaining=max(0, remaining - 1),
        )

    # ── generation ───────────────────────────────────────────────────────────

    def _line(
        self,
        *,
        profile: "RoleProfile",
        question: dict[str, Any],
        answer: str,
        depth: Depth,
        turn_index: int,
        turn_count: int,
    ) -> tuple[str, str]:
        """One follow-up line, or ``("", "")`` when the model cannot supply one."""
        client = self.client
        if client is None or not client.available:
            return "", ""

        self._calls.append(depth.value)
        try:
            data = client.complete_json(
                system=_FOLLOWUP_SYSTEM,
                user=build_follow_up_prompt(
                    profile,
                    question,
                    answer,
                    depth=depth,
                    turn_index=turn_index,
                    turn_count=turn_count,
                ),
                temperature=0.5,
                max_tokens=400,
            )
        except (ProviderError, ProviderUnavailable) as exc:
            logger.warning("follow-up generation failed: %s", exc)
            return "", ""

        prompt = str(data.get("question") or "").strip()
        if len(prompt) < 10:
            return "", ""
        # A follow-up longer than this is a lecture. Cap it and say so.
        prompt = prompt[:300]
        anchor = str(data.get("anchor_skill") or "").strip()[:60]
        return prompt, anchor


def followup_budget_note(plan_total: int) -> str:
    """Human-readable budget, for the transcript header. Not a score."""
    budget = max(0, min(FOLLOWUP_BUDGET_CAP, int(plan_total * FOLLOWUP_BUDGET_RATIO)))
    return (
        f"Up to {budget} follow-up question"
        f"{'' if budget == 1 else 's'} may be added to the {plan_total} planned "
        "exchanges, chosen by how each answer lands."
    )


__all__ = [
    "Depth",
    "FollowUpAction",
    "FollowUpDecision",
    "FollowUpEngine",
    "FOLLOWUP_BUDGET_CAP",
    "FOLLOWUP_BUDGET_RATIO",
    "MIN_PROBE_WORDS",
    "build_follow_up_prompt",
    "decide_depth",
    "followup_budget_note",
    "re_words",
]
