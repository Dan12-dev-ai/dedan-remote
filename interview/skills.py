"""
Skill extraction — turn a raw listing into a normalised skill vector.

The interview is only worth running if its questions are about *this* job. The
index stores skill evidence as free text (`tags`, title, description) with no
skills column anywhere in the schema, so the vector has to be derived. It is
derived deterministically here, from an explicit taxonomy, with no model call:
a question set built on a hallucinated skill is worse than no question set.

Design notes:

- **Evidence-weighted.** A skill in the title outranks the same skill buried in
  a 4,000-character description, because that is how a human reads a posting.
- **Alias-aware.** "K8s" / "Kubernetes" / "EKS" are one skill, not three, or the
  radar chart double-counts and the question set wastes two slots on one topic.
- **Deterministic ordering.** Skills come back in descending evidence order with
  stable tie-breaks, so the same listing always produces the same plan. A model
  that reorders questions on every run makes two interviews incomparable.
- **Honest about absence.** An empty vector is returned as empty. Nothing is
  invented to pad it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

# ── taxonomy ────────────────────────────────────────────────────────────────
#
# `aliases` are matched as whole words where they are alphanumeric, so "go" can
# never match inside "google". `weight` is the per-field multiplier that turns
# a mention into evidence.

SKILL_TAXONOMY: dict[str, dict[str, Any]] = {
    # languages
    "python": {"aliases": ["python", "python3", "py"], "family": "language", "weight": 1.0},
    "sql": {"aliases": ["sql", "t-sql", "pl/sql"], "family": "language", "weight": 1.0},
    "javascript": {
        "aliases": ["javascript", "js", "ecmascript"],
        "family": "language",
        "weight": 1.0,
    },
    "typescript": {"aliases": ["typescript", "ts"], "family": "language", "weight": 1.0},
    "java": {"aliases": ["java"], "family": "language", "weight": 1.0},
    "go": {"aliases": ["golang", "go lang"], "family": "language", "weight": 1.0},
    "rust": {"aliases": ["rust", "cargo"], "family": "language", "weight": 1.0},
    "ruby": {"aliases": ["ruby", "rails", "ruby on rails"], "family": "language", "weight": 0.9},
    "php": {"aliases": ["php", "laravel", "symfony"], "family": "language", "weight": 0.9},
    "kotlin": {"aliases": ["kotlin"], "family": "language", "weight": 0.9},
    "scala": {"aliases": ["scala", "spark"], "family": "language", "weight": 0.9},
    "cpp": {"aliases": ["c++", "cpp"], "family": "language", "weight": 0.9},
    "csharp": {"aliases": ["c#", "csharp", ".net", "dotnet"], "family": "language", "weight": 0.9},
    "r": {"aliases": ["r programming", "rstudio"], "family": "language", "weight": 0.8},
    "bash": {"aliases": ["bash", "shell scripting", "shell"], "family": "language", "weight": 0.8},
    # data / ml
    "machine-learning": {
        "aliases": ["machine learning", "ml", "deep learning"],
        "family": "data",
        "weight": 1.0,
    },
    "nlp": {
        "aliases": ["nlp", "natural language processing", "text classification"],
        "family": "data",
        "weight": 1.0,
    },
    "computer-vision": {
        "aliases": ["computer vision", "image classification", "object detection"],
        "family": "data",
        "weight": 1.0,
    },
    "llm": {
        "aliases": ["llm", "large language model", "prompt engineering", "fine-tuning"],
        "family": "data",
        "weight": 1.0,
    },
    "data-annotation": {
        "aliases": ["data annotation", "annotation", "labelling", "labeling", "data labeling"],
        "family": "data",
        "weight": 1.0,
    },
    "data-analysis": {
        "aliases": ["data analysis", "data analytics", "eda", "exploratory data analysis"],
        "family": "data",
        "weight": 0.9,
    },
    "statistics": {
        "aliases": ["statistics", "statistical", "hypothesis testing"],
        "family": "data",
        "weight": 0.9,
    },
    "pandas": {"aliases": ["pandas", "numpy", "polars"], "family": "data", "weight": 0.9},
    # systems
    "system-design": {
        "aliases": ["system design", "distributed systems", "scalability", "architecture"],
        "family": "systems",
        "weight": 1.0,
    },
    "databases": {
        "aliases": [
            "database",
            "nosql",
            "relational",
            "indexing",
            "query optimisation",
            "query optimization",
        ],
        "family": "systems",
        "weight": 0.9,
    },
    "kubernetes": {
        "aliases": ["kubernetes", "k8s", "eks", "gke", "helm"],
        "family": "systems",
        "weight": 1.0,
    },
    "docker": {
        "aliases": ["docker", "containerisation", "containerization"],
        "family": "systems",
        "weight": 1.0,
    },
    "aws": {
        "aliases": ["aws", "amazon web services", "ec2", "s3"],
        "family": "systems",
        "weight": 1.0,
    },
    "gcp": {"aliases": ["gcp", "google cloud", "bigquery"], "family": "systems", "weight": 1.0},
    "api-design": {
        "aliases": ["api design", "rest api", "restful", "graphql", "openapi"],
        "family": "systems",
        "weight": 0.9,
    },
    # quality / process
    "testing": {
        "aliases": ["testing", "unit test", "pytest", "integration test", "tdd"],
        "family": "quality",
        "weight": 0.9,
    },
    "cicd": {
        "aliases": ["ci/cd", "cicd", "continuous integration", "jenkins", "github actions"],
        "family": "quality",
        "weight": 0.9,
    },
    "git": {
        "aliases": ["git", "version control", "github", "gitlab"],
        "family": "quality",
        "weight": 0.7,
    },
    # product / comms — the behavioural-signal side
    "communication": {
        "aliases": ["communication", "communicating", "written english", "documentation"],
        "family": "communication",
        "weight": 0.8,
    },
    "customer-facing": {
        "aliases": ["customer facing", "customer-facing", "client facing", "client-facing"],
        "family": "communication",
        "weight": 0.8,
    },
    "project-management": {
        "aliases": ["project management", "stakeholder", "roadmap", "jira"],
        "family": "communication",
        "weight": 0.7,
    },
}


# Precompiled matchers, built once at import. Word-boundaried so short aliases
# ("go", "r", "js") cannot match inside an unrelated word.
def _pattern(aliases: list[str]) -> re.Pattern[str]:
    """One word-boundaried alternation over every alias of a skill."""
    body = "|".join(re.escape(alias) for alias in sorted(aliases, key=len, reverse=True))
    return re.compile(rf"(?<![\w+])(?:{body})(?![\w+])", re.IGNORECASE)


_MATCHERS: list[tuple[str, re.Pattern[str]]] = [
    (skill, _pattern(spec["aliases"])) for skill, spec in SKILL_TAXONOMY.items()
]

# Field weights: how much a mention in that field is worth.
FIELD_WEIGHTS = {
    "title": 3.0,
    "tags": 2.0,
    "category": 1.2,
    "experience_hint": 0.8,
    "description": 1.0,
}


@dataclass(frozen=True)
class Skill:
    """One required skill with the evidence that produced it."""

    name: str
    family: str
    evidence: float
    fields: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": skill_label(self.name),
            "family": self.family,
            "evidence": round(self.evidence, 2),
            "fields": list(self.fields),
        }


@dataclass
class SkillVector:
    """
    The ordered skill vector for one listing.

    ``primary`` drives question focus; ``supporting`` fills out the radar chart
    and the coverage report.
    """

    skills: list[Skill] = field(default_factory=list)
    #: True when nothing in the taxonomy matched. Callers must handle this
    #: explicitly rather than presenting an empty vector as a real analysis.
    empty: bool = True

    @property
    def primary(self) -> list[str]:
        return [s.name for s in self.skills[:5]]

    @property
    def supporting(self) -> list[str]:
        return [s.name for s in self.skills[5:12]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "skills": [s.to_dict() for s in self.skills],
            "primary": self.primary,
            "supporting": self.supporting,
            "empty": self.empty,
        }

    def brief(self, limit: int = 8) -> str:
        """Compact rendering for a prompt."""
        if not self.skills:
            return "(no recognised skills — ask broad behavioural questions only)"
        return ", ".join(skill_label(s) for s in (self.primary + self.supporting)[:limit])


_LABELS = {
    "machine-learning": "machine learning",
    "computer-vision": "computer vision",
    "data-annotation": "data annotation",
    "data-analysis": "data analysis",
    "system-design": "system design",
    "api-design": "API design",
    "nlp": "NLP",
    "llm": "LLM work",
    "cicd": "CI/CD",
    "customer-facing": "customer-facing",
    "project-management": "project management",
}


def skill_label(name: str) -> str:
    """Human label for a canonical skill key."""
    return _LABELS.get(name, name.replace("-", " "))


def extract_skills(job: dict[str, Any]) -> SkillVector:
    """
    Derive the skill vector from whatever fields the listing actually has.

    Missing fields are skipped rather than defaulted to empty strings, so a
    sparse listing cannot dilute the evidence of a rich one.
    """
    scores: dict[str, float] = {}
    fields_hit: dict[str, set[str]] = {}

    for field_name, weight in FIELD_WEIGHTS.items():
        raw = job.get(field_name)
        text = _as_text(raw)
        if not text:
            continue
        for skill, pattern in _MATCHERS:
            hits = pattern.findall(text)
            if not hits:
                continue
            # Multiple mentions in one field add up, but with diminishing
            # returns — a job that says "Kubernetes" nine times is not nine
            # times more about Kubernetes.
            mentions = min(len(hits), 3)
            scores[skill] = scores.get(skill, 0.0) + weight * mentions
            fields_hit.setdefault(skill, set()).add(field_name)

    if not scores:
        return SkillVector(skills=[], empty=True)

    ranked = sorted(
        scores.items(),
        # Descending evidence, then canonical name — a total order, so the plan
        # is reproducible across runs.
        key=lambda kv: (-round(kv[1], 6), kv[0]),
    )

    skills = [
        Skill(
            name=name,
            family=SKILL_TAXONOMY[name]["family"],
            evidence=score,
            fields=tuple(sorted(fields_hit.get(name, set()), key=FIELD_WEIGHTS.get, reverse=True)),
        )
        for name, score in ranked
    ]
    return SkillVector(skills=skills, empty=False)


def overlap_ratio(vector: SkillVector, other: Iterable[str]) -> float:
    """
    Fraction of ``other``'s skills that this vector also covers.

    Used by the alert worker to decide whether a new listing is worth an email.
    Deliberately asymmetric: a listing whose skills are a subset of the
    candidate's profile is a strong match, and that is the case that matters.
    """
    have = {s.name for s in vector.skills}
    wanted = {s for s in other if s}
    if not wanted:
        return 0.0
    return len(have & wanted) / len(wanted)


def _as_text(raw: Any) -> str:
    """Flatten a listing field into searchable text."""
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, (list, tuple, set)):
        return " ".join(_as_text(item) for item in raw)
    if isinstance(raw, dict):
        return " ".join(f"{key} {value}" for key, value in raw.items())
    return str(raw)
