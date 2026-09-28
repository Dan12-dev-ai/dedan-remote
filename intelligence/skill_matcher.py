"""
Skill Matcher — evaluates skill-level match and experience requirements.
Prioritizes beginner-friendly opportunities and rejects over-specialized roles.
"""

from __future__ import annotations

from models.job import Job
from utils.logger import get_logger

logger = get_logger(__name__)

# ── Experience level classification keywords ──────────────────────────────
NO_EXPERIENCE: list[str] = [
    "no experience", "no experience required", "no experience needed",
    "no prior experience", "no previous experience", "no experience necessary",
    "anyone can", "start today", "start immediately", "immediate start",
    "open to all", "no degree", "no degree required", "no qualifications",
    "no skills required", "train provided", "training provided",
    "full training provided", "on the job training", "learn on the job",
    "entry", "entry level", "entry-level", "beginner", "absolute beginner",
    "no background", "any background", "welcome to apply",
    "no interview", "quick apply", "instant", "zero experience",
]

BEGINNER: list[str] = [
    "beginner", "beginner friendly", "beginner-level", "basic",
    "fundamental", "introductory", "basics", "startup",
    "some experience", "basic knowledge", "basic understanding",
    "familiar with", "have used", "know the basics",
    "some familiarity", "basic skills", "1 year", "one year",
    "internship", "intern", "trainee", "traineeship",
    "apprenticeship", "apprentice", "graduate", "new graduate",
]

ENTRY_LEVEL: list[str] = [
    "entry level", "entry-level", "junior", "junior level", "jr",
    "associate", "early career", "early-career",
    "0-2 years", "0-2", "0 to 2 years", "0 to 2",
    "1-2 years", "1-2", "1 to 2 years", "1 to 2",
    "2 years", "two years", "2+ years",
]

JUNIOR: list[str] = [
    "junior", "jr", "junior level", "mid junior",
    "2-3 years", "2-3", "2 to 3 years", "2 to 3",
    "some professional experience", "a few years",
]

INTERMEDIATE: list[str] = [
    "intermediate", "mid-level", "mid level", "midlevel",
    "3+ years", "3+", "3 to 5 years", "3-5 years",
    "4 years", "4+ years", "5 years", "proficient",
    "working knowledge", "solid understanding",
]

SENIOR: list[str] = [
    "senior", "sr", "senior level", "lead",
    "5+ years", "5+", "5 to 7 years", "5-7 years",
    "7+ years", "7+", "8+ years", "10 years",
    "extensive experience", "many years",
]

# ── Highly specialized positions to reject ────────────────────────────────
HIGHLY_SPECIALIZED: list[str] = [
    "agi engineer", "ai research scientist", "machine learning scientist",
    "deep learning researcher", "principal ai engineer", "distinguished engineer",
    "staff software engineer", "senior software engineer", "lead software engineer",
    "solutions architect", "cloud architect", "platform architect",
    "infrastructure architect", "site reliability engineer",
    "mlops engineer", "data engineer", "distributed systems engineer",
    "compiler engineer", "gpu engineer", "robotics engineer",
    "autonomous vehicle engineer", "security research engineer",
    "blockchain protocol engineer",
]

# ── High-priority opportunity types ───────────────────────────────────────
HIGH_PRIORITY_TITLES: list[tuple[list[str], list[str]]] = [
    # AI Work
    (["ai data labeling", "data labeler", "data annotator", "ai annotator",
      "ai trainer", "ai evaluator", "rhlf", "rlhf",
      "prompt evaluator", "prompt writer", "prompt engineer",
      "ai content reviewer", "ai conversation reviewer",
      "ai safety rater", "ai safety",
      "search evaluator", "search rater",
      "human feedback", "human in the loop",
      "ai training", "ai evaluation"],
     ["ai", "artificial intelligence", "data annotation", "labeling",
      "training data", "machine learning"]),

    # Data Annotation
    (["image annotation", "video annotation", "audio annotation",
      "text annotation", "ocr annotation", "bounding box",
      "segmentation", "data verification", "data validation",
      "data quality", "data categorization", "classification",
      "content moderation", "content review"],
     ["annotation", "data", "labeling", "tagging"]),

    # Language Work
    (["translation", "translator", "transcriber", "transcription",
      "captioning", "subtitling", "proofreading", "proofreader",
      "english evaluation", "english rater", "linguist",
      "localization", "copy editing"],
     ["language", "english", "translation", "writing"]),

    # Remote Freelancing
    (["virtual assistant", "customer support", "community moderator",
      "research assistant", "online tutor", "tutor",
      "data entry", "spreadsheet", "web research",
      "internet research", "market research",
      "admin support", "administrative assistant"],
     ["remote", "freelance", "virtual", "online"]),

    # Testing
    (["website testing", "app testing", "software testing",
      "user testing", "ux testing", "qa tester",
      "usability testing", "beta tester", "game tester",
      "test engineer", "quality assurance"],
     ["testing", "qa", "quality assurance"]),

    # Microtasks
    (["survey", "microtask", "clickworker", "crowdworker",
      "product categorization", "content classification",
      "metadata", "document verification",
      "information extraction", "data collection"],
     ["microtask", "flexible", "part time"]),

    # Technical Beginner
    (["junior python", "junior developer", "junior web",
      "junior frontend", "junior backend",
      "junior automation", "junior qa",
      "technical support", "technical writer",
      "documentation", "open source",
      "no-code", "low-code", "nocode", "lowcode",
      "entry level developer", "entry level engineer"],
     ["python", "javascript", "html", "css", "web",
      "technical", "development"]),
]

# Keywords that indicate the role is truly beginner-accessible
BEGINNER_FRIENDLY_KEYWORDS: list[str] = [
    "no experience", "training provided", "full training",
    "learn", "beginner", "entry level", "no degree",
    "anyone", "start today", "no skills required",
    "quick apply", "no interview", "welcome",
    "any background", "all levels", "flexible",
    "work from home", "remote", "online",
    "get paid to learn", "paid training",
]


class SkillMatcher:
    """
    Evaluates skill-level match and experience requirements.

    Returns a SkillMatchScore (0–100) highlighting how well the opportunity
    fits a beginner-to-early-career skill profile.
    """

    EXPERIENCE_LEVELS = [
        ("no_experience", NO_EXPERIENCE, 100),
        ("beginner", BEGINNER, 90),
        ("entry_level", ENTRY_LEVEL, 80),
        ("junior", JUNIOR, 70),
        ("intermediate", INTERMEDIATE, 40),
        ("senior", SENIOR, 10),
    ]

    def score(self, job: Job) -> int:
        """
        Compute skill-match score (0–100).

        100 = Perfect for a beginner with no experience
        0   = Requires senior/expert skills
        """
        text = self._build_text(job).lower()

        # ── Check for highly specialized positions → reject ─────────
        for spec_title in HIGHLY_SPECIALIZED:
            if spec_title in job.title.lower():
                # Allow if explicitly entry-level
                if any(phrase in text for phrase in (
                    "entry level", "junior", "graduate",
                    "internship", "new graduate", "no experience",
                    "training provided",
                )):
                    return 60
                logger.debug("SkillMatch: REJECTED specialized role: %s", job.title)
                return 0

        # ── Detect experience level from description ────────────────
        for level_name, keywords, base_score in self.EXPERIENCE_LEVELS:
            if any(kw in text for kw in keywords):
                score = base_score
                # Bonus for beginner-friendly keywords
                if level_name in ("no_experience", "beginner", "entry_level"):
                    bonus = sum(5 for kw in BEGINNER_FRIENDLY_KEYWORDS if kw in text)
                    score = min(100, score + bonus)
                return score

        # ── Fallback: look at title for clues ───────────────────────
        title_lower = job.title.lower()

        # Check if it's a high-priority opportunity
        for title_keywords, _ in HIGH_PRIORITY_TITLES:
            if any(kw in title_lower for kw in title_keywords):
                # Give these a solid score even without explicit level
                score = 70
                bonus = sum(5 for kw in BEGINNER_FRIENDLY_KEYWORDS if kw in text)
                return min(100, score + bonus)

        # Default: assume accessible but not explicitly beginner-friendly
        return 50

    def get_experience_level(self, job: Job) -> str:
        """Return classified experience level string."""
        text = self._build_text(job).lower()

        for level_name, keywords, _ in self.EXPERIENCE_LEVELS:
            if any(kw in text for kw in keywords):
                return level_name.replace("_", " ").title()

        return "Unknown"

    def is_specialized_role(self, job: Job) -> bool:
        """Check if this is a highly specialized role that should be rejected."""
        title_lower = job.title.lower()
        return any(spec in title_lower for spec in HIGHLY_SPECIALIZED)

    def is_high_priority(self, job: Job) -> bool:
        """Check if this role matches high-priority opportunity types."""
        title_lower = job.title.lower()
        text = self._build_text(job).lower()

        for title_keywords, desc_keywords in HIGH_PRIORITY_TITLES:
            if any(kw in title_lower for kw in title_keywords):
                return True
            if any(kw in text for kw in desc_keywords):
                if any(kw in title_lower for kw in title_keywords):
                    return True

        return False

    def _build_text(self, job: Job) -> str:
        """Build searchable text from job."""
        parts = [job.title, job.description or "", " ".join(job.tags)]
        return " ".join(parts)