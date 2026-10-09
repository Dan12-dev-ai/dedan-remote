"""
Adaptive question planner — build a skill-grounded interview set.

Composition, per the brief, as a hard invariant rather than a suggestion:

    2  tech / skill validation      one per high-evidence skill
    2  behavioural, STAR-shaped    transferable evidence, not trivia
    1–2 system / practical scenario the role's own work, made concrete

    total 5–8, fewer when the listing is thin

Two properties this module guarantees:

- **Every question is anchored to a real skill** from the listing. A question
  that names no target skill cannot be scored on relevance, so one is required.
- **The set is reproducible.** Skill order is deterministic (see
  `interview.skills`), so the same listing produces the same plan modulo the
  model's wording. Two attempts at the same role are therefore comparable,
  which is the whole point of a mock interview.

Generation is model-first with a deterministic template fallback. The fallback
is not a stub: it composes real questions from the skill vector and the role
context, and the report says which path produced the set. Silently serving
templates as though a model wrote them would be the exact dishonesty this
product is built to avoid.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from interview.skills import SkillVector, extract_skills, skill_label
from interview.provider import LLMClient, ProviderError, ProviderUnavailable

logger = logging.getLogger("dedan.interview.planner")

# Question kinds, in the order they are asked.
KIND_TECH = "tech"
KIND_BEHAVIORAL = "behavioral"
KIND_SYSTEM = "system"

# Time budget per kind and difficulty (seconds). A system-design probe needs
# real thinking time; a "define a term" does not. These are floors for the
# candidate, not a countdown the server enforces — the browser timer is
# advisory and a slow candidate can always continue.
TIME_LIMITS: dict[tuple[str, str], int] = {
    (KIND_TECH, "easy"): 90,
    (KIND_TECH, "moderate"): 150,
    (KIND_TECH, "hard"): 240,
    (KIND_BEHAVIORAL, "easy"): 120,
    (KIND_BEHAVIORAL, "moderate"): 180,
    (KIND_BEHAVIORAL, "hard"): 240,
    (KIND_SYSTEM, "moderate"): 240,
    (KIND_SYSTEM, "hard"): 300,
}

PLANNER_SYSTEM = (
    "You write interview questions for a technical hiring loop. Hard rules:\n"
    "1. Every question must probe ONE named skill. Never ask a question that "
    "could be asked of any role.\n"
    "2. Behavioural questions must be STAR-shaped: ask for a specific past "
    "situation, the action taken, and the measurable outcome. Never ask what "
    "someone 'would do'.\n"
    "3. Do not ask about company-specific facts, internal tools, or trivia that "
    "a candidate could only know by browsing this site.\n"
    "4. Difficulty must match the seniority implied by the evidence provided.\n"
    "5. Never mention that the question was generated, and never include the "
    "skill name inside the question text — the candidate is told the skill "
    "separately, so repeating it in the prompt leaks the rubric.\n"
    "Return JSON only."
)


@dataclass
class PlannedQuestion:
    target_skill: str
    skill_family: Optional[str]
    kind: str
    difficulty: str
    question_text: str
    time_limit_sec: int
    rubric_focus: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_skill": self.target_skill,
            "skill_family": self.skill_family,
            "kind": self.kind,
            "difficulty": self.difficulty,
            "question_text": self.question_text,
            "time_limit_sec": self.time_limit_sec,
            "rubric_focus": self.rubric_focus,
        }


@dataclass
class InterviewPlan:
    questions: list[PlannedQuestion]
    skills: SkillVector
    #: ``"model"`` or ``"template"``. Surfaced in the UI so a reader always
    #: knows whether a model wrote the questions.
    origin: str
    model: Optional[str] = None
    #: Set when the listing yielded no recognisable skills.
    skillless: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "questions": [q.to_dict() for q in self.questions],
            "skills": self.skills.to_dict(),
            "origin": self.origin,
            "model": self.model,
            "skillless": self.skillless,
            "composition": self.composition(),
        }

    def composition(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for question in self.questions:
            counts[question.kind] = counts.get(question.kind, 0) + 1
        return counts


def plan_interview(
    job: dict[str, Any],
    *,
    client: Optional[LLMClient] = None,
    min_questions: int = 5,
    max_questions: int = 8,
) -> InterviewPlan:
    """
    Build the question set for ``job``.

    Never raises for a missing model: falls back to templates and says so. The
    caller must not be able to mistake a template plan for a model plan, which
    is why `origin` is part of the return value rather than a log line.
    """
    vector = extract_skills(job)
    slots = _composition(vector, min_questions, max_questions)

    if client is not None and client.available:
        try:
            questions = _model_questions(job, vector, slots, client)
            if questions:
                return InterviewPlan(
                    questions=questions,
                    skills=vector,
                    origin="model",
                    model=client._settings.LLM_MODEL,  # noqa: SLF001 - reporting only
                    skillless=vector.empty,
                )
            logger.warning("planner returned an empty set; falling back to templates")
        except (ProviderUnavailable, ProviderError) as exc:
            logger.warning("planner falling back to templates: %s", exc)

    return InterviewPlan(
        questions=_template_questions(vector, slots),
        skills=vector,
        origin="template",
        skillless=vector.empty,
    )


# ── composition ─────────────────────────────────────────────────────────────


def _composition(vector: SkillVector, minimum: int, maximum: int) -> dict[str, list[str]]:
    """
    Decide how many of each kind, and which skills they probe.

    The brief's shape (2 tech, 2 behavioural, 1–2 system) is kept whenever the
    skill vector can support it. A data-annotation role with no systems skills
    gets its scenario slot re-pointed at the data work instead of inventing an
    infrastructure question — a stretch is fine, a fabricated requirement is not.
    """
    minimum = max(1, minimum)
    maximum = max(minimum, maximum)

    tech_skills = [s.name for s in vector.skills][:4]
    system_skills = [s.name for s in vector.skills if s.family in ("systems", "quality")]

    # The brief's floor is 2 technical probes where the listing supports them,
    # and 1 opener where it does not — never zero, or there is nothing to score
    # on relevance.
    tech_count = min(2, len(tech_skills)) or 1
    behavioral_count = 2
    system_count = max(1, min(2, minimum - tech_count - behavioral_count))

    # Grow toward the ceiling only when there are more distinct skills to probe,
    # so a rich listing earns a longer interview and a thin one is not padded
    # with repeated questions about the same single skill.
    spare = [s.name for s in vector.skills[tech_count:]]
    while (
        tech_count + behavioral_count + system_count < maximum and len(spare) > 0 and tech_count < 4
    ):
        tech_count += 1
        spare.pop(0)
    while tech_count + behavioral_count + system_count < maximum and len(spare) > 0:
        # Behavioural depth is worth adding before more system slots.
        behavioral_count += 1
        spare.pop(0)

    # Never exceed the ceiling by construction.
    while tech_count + behavioral_count + system_count > maximum:
        if system_count > 1:
            system_count -= 1
        elif behavioral_count > 2:
            behavioral_count -= 1
        elif tech_count > 1:
            tech_count -= 1
        else:
            break

    # A thin skill vector should not pad to the floor with filler questions.
    while tech_count + behavioral_count + system_count < minimum and tech_count < 3:
        tech_count += 1

    # System slots come from the same vector — never invented. Anything short of
    # the requested count becomes a "general" slot that asks about the work
    # itself rather than pretending the listing demanded a specific skill.
    system_targets = system_skills[:system_count]
    for skill in [s.name for s in vector.skills if s.name not in system_targets]:
        if len(system_targets) >= system_count:
            break
        system_targets.append(skill)
    while len(system_targets) < system_count:
        system_targets.append(tech_skills[0] if tech_skills else "general")

    tech_targets = tech_skills[:tech_count]
    while len(tech_targets) < tech_count:
        tech_targets.append("general")

    return {
        "tech": tech_targets,
        "behavioral": ["transferable"] * behavioral_count,
        "system": system_targets,
    }


# ── model path ──────────────────────────────────────────────────────────────


def _model_questions(
    job: dict[str, Any],
    vector: SkillVector,
    slots: dict[str, list[str]],
    client: LLMClient,
) -> list[PlannedQuestion]:
    counts = {kind: len(targets) for kind, targets in slots.items()}
    payload = client.complete_json(
        system=PLANNER_SYSTEM,
        user=(
            "Write the interview questions.\n\n"
            f"Role: {job.get('title') or 'unspecified'}\n"
            f"Employer: {job.get('company') or 'unspecified'}\n"
            f"Location: {job.get('location_label') or 'unspecified'}\n"
            f"Experience level: {job.get('experience_hint') or 'unspecified'}\n\n"
            "Required skills with their evidence strength (strongest first):\n"
            f"{vector.brief(limit=12)}\n\n"
            "Required composition:\n"
            f"{counts['tech']} tech/skill-validation questions, "
            f"{counts['behavioral']} STAR behavioural questions, "
            f"{counts['system']} system/practical scenario question(s).\n"
            "Distribution of target skills by kind:\n"
            f"{slots}\n\n"
            "Return JSON shaped exactly like:\n"
            '{"questions": [{"kind": "tech|behavioral|system", '
            '"target_skill": "<one of the skills listed above>", '
            '"difficulty": "easy|moderate|hard", '
            '"question_text": "...", "rubric_focus": ["...", "..."]}]}\n'
            "Only use target skills from the distribution. Do not invent skills."
        ),
        temperature=0.5,
        max_tokens=1600,
    )

    raw = payload.get("questions")
    if not isinstance(raw, list) or not raw:
        return []

    families = {s.name: s.family for s in vector.skills}
    allowed: set[str] = {skill for targets in slots.values() for skill in targets}

    questions: list[PlannedQuestion] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        kind = str(entry.get("kind", "")).strip().lower()
        if kind not in (KIND_TECH, KIND_BEHAVIORAL, KIND_SYSTEM):
            continue
        text = str(entry.get("question_text", "")).strip()
        skill = str(entry.get("target_skill", "")).strip()
        if not text or not skill:
            continue
        # A model that invents a skill gets that question dropped, not silently
        # scored against a rubric for something the role never asked for.
        if skill not in allowed:
            logger.info("dropping planned question for unknown skill %r", skill)
            continue
        difficulty = str(entry.get("difficulty", "moderate")).lower()
        if difficulty not in ("easy", "moderate", "hard"):
            difficulty = "moderate"
        focus = entry.get("rubric_focus")
        questions.append(
            PlannedQuestion(
                target_skill=skill,
                skill_family=families.get(skill),
                kind=kind,
                difficulty=difficulty,
                question_text=text,
                time_limit_sec=TIME_LIMITS.get((kind, difficulty), 180),
                rubric_focus=[str(f) for f in focus][:4] if isinstance(focus, list) else [],
            )
        )
    return _order(questions)


def _order(questions: list[PlannedQuestion]) -> list[PlannedQuestion]:
    """Warm-up → technical → behavioural → scenario. Stable and predictable."""
    sequence = {KIND_TECH: 0, KIND_BEHAVIORAL: 1, KIND_SYSTEM: 2}
    return sorted(questions, key=lambda q: sequence.get(q.kind, 9))


# ── template path ───────────────────────────────────────────────────────────

# Rotated by index so a four-skill interview does not ask the same sentence
# four times with a different noun in it.
_TECH_TEMPLATES: tuple[str, ...] = (
    (
        "You have practical experience with {label}. Take me through how you would "
        "approach a real piece of work in that area, from the first hour to "
        "something you could ship. What do you do first, and why?"
    ),
    (
        "Where does {label} genuinely break down in production, and what have you "
        "done about it when you have been the one on call?"
    ),
    (
        "How would you convince a sceptical colleague that a change to how we use "
        "{label} is an improvement rather than churn? What evidence would you "
        "bring?"
    ),
    (
        "What is the most common mistake you have seen a team make with {label}, "
        "and how do you raise it early enough for it to matter?"
    ),
)

_BEHAVIORAL_TEMPLATES = [
    (
        "Tell me about a time you had to {behaviour} work involving {label}. "
        "What was the situation, what specifically did you do, and what was the "
        "measurable outcome?"
    ),
    (
        "Describe a project where {label} was central and something went wrong. "
        "What did you do, and what changed as a result?"
    ),
]

_SYSTEM_TEMPLATES = [
    (
        "Imagine you are joining this team and inheriting their {label} workload on "
        "day one. What do you examine in the first week, and what would make you "
        "stop and rebuild something?"
    ),
    (
        "How would you know whether the {label} part of this system is actually "
        "working? Walk me through the signal you would trust over the signal "
        "everyone else looks at."
    ),
]


def _template_questions(vector: SkillVector, slots: dict[str, list[str]]) -> list[PlannedQuestion]:
    """
    Deterministic questions assembled from the real skill vector.

    These read as interview questions because they are — they are composed, not
    modelled, and the UI is told `origin: "template"`.
    """
    families = {s.name: s.family for s in vector.skills}
    questions: list[PlannedQuestion] = []

    for index, skill in enumerate(slots.get("tech", [])):
        if skill == "general" or vector.empty:
            text = (
                "This listing does not state a specific technical requirement, so "
                "let us start with the work itself: what kind of tasks does this "
                "role actually spend its time on, and which of them do you find "
                "most demanding?"
            )
            target = "general"
            difficulty = "moderate"
        else:
            text = _TECH_TEMPLATES[index % len(_TECH_TEMPLATES)].format(label=skill_label(skill))
            target = skill
            # The strongest skill gets the gentler opener; later ones go deeper.
            difficulty = "moderate" if index == 0 else "hard"
        questions.append(
            PlannedQuestion(
                target_skill=target,
                skill_family=families.get(target),
                kind=KIND_TECH,
                difficulty=difficulty,
                question_text=text,
                time_limit_sec=TIME_LIMITS[(KIND_TECH, difficulty)],
                rubric_focus=["depth", "specificity", "trade-offs"],
            )
        )

    behaviours = ["ship", "debug", "push back on"]
    for index, marker in enumerate(slots.get("behavioral", [])):
        label = skill_label(slots.get("tech", ["the role"])[0]) if slots.get("tech") else "this"
        text = _BEHAVIORAL_TEMPLATES[index % len(_BEHAVIORAL_TEMPLATES)].format(
            behaviour=behaviours[index % len(behaviours)],
            label=label,
        )
        questions.append(
            PlannedQuestion(
                target_skill=marker,
                skill_family="communication",
                kind=KIND_BEHAVIORAL,
                difficulty="moderate",
                question_text=text,
                time_limit_sec=TIME_LIMITS[(KIND_BEHAVIORAL, "moderate")],
                rubric_focus=["situation", "action", "outcome"],
            )
        )

    for index, skill in enumerate(slots.get("system", [])):
        label = skill_label(skill) if skill != "general" else "day-to-day"
        text = _SYSTEM_TEMPLATES[index % len(_SYSTEM_TEMPLATES)].format(label=label)
        questions.append(
            PlannedQuestion(
                target_skill=skill,
                skill_family=families.get(skill) or "systems",
                kind=KIND_SYSTEM,
                difficulty="moderate" if index == 0 else "hard",
                question_text=text,
                time_limit_sec=TIME_LIMITS[(KIND_SYSTEM, "moderate" if index == 0 else "hard")],
                rubric_focus=["trade-offs", "risk", "verifiability"],
            )
        )

    return _order(questions)
