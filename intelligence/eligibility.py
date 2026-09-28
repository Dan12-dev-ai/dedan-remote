"""
Eligibility Engine — evaluates geographic, payment, and access eligibility
for applicants based in Ethiopia.
"""

from __future__ import annotations

from typing import Optional

from models.job import Job
from utils.logger import get_logger

logger = get_logger(__name__)

# Countries known to restrict remote work payments to Ethiopia
RESTRICTIVE_COUNTRIES: set[str] = {
    "iran",
    "syria",
    "north korea",
    "cuba",
    "sudan",
    "crimea",
    "venezuela",
    "myanmar",
    "belarus",
}

# Payment platforms common in Ethiopia
ETHIOPIA_FRIENDLY_PAYMENT: set[str] = {
    "paypal",
    "payoneer",
    "wise",
    "cryptocurrency",
    "crypto",
    "airtm",
    "bitcoin",
    "usdt",
    "wire transfer",
    "telebirr",
}

# Countries known to work well with Ethiopian freelancers
ETHIOPIA_FRIENDLY_COUNTRIES: set[str] = {
    "worldwide",
    "global",
    "remote",
    "anywhere",
    "united states",
    "united kingdom",
    "canada",
    "australia",
    "germany",
    "netherlands",
    "switzerland",
    "singapore",
    "uae",
    "united arab emirates",
}

# Indicators that payment may be restricted for Ethiopia
RESTRICTION_INDICATORS: list[str] = [
    "us citizens only",
    "us residents only",
    "must be in us",
    "north america only",
    "us only",
    "united states only",
    "uk only",
    "europe only",
    "eu only",
    "eu residents",
    "must be located in",
    "must reside in",
    "local candidates only",
    "onsite required",
    "on-site required",
    "hybrid",
]


class EligibilityEngine:
    """
    Evaluates whether a job opportunity is realistically accessible
    to someone based in Ethiopia.

    Checks:
      - Geographic restrictions
      - Payment method compatibility
      - Country-specific bans or sanctions
      - Residency/citizenship requirements
    """

    def score(self, job: Job) -> int:
        """
        Score geographic/payment eligibility from 0–100.

        100 = Fully accessible worldwide
        0   = Blocked for Ethiopian applicants
        """
        text = self._build_search_text(job)
        score = 100

        # ── Hard blocks (score = 0) ─────────────────────────────────
        if self._is_blocked_for_ethiopia(text):
            logger.debug("Eligibility: BLOCKED for %s @ %s", job.title, job.company)
            return 0

        # ── Geographic restrictions ──────────────────────────────────
        if self._has_geographic_restriction(text):
            score -= 40

        if job.country and job.country.lower() not in ETHIOPIA_FRIENDLY_COUNTRIES:
            country_lower = job.country.lower()
            if any(restricted in country_lower for restricted in {"only", "required", "must"}):
                score -= 50
            else:
                score -= 20

        # ── Remote/work-from-home requirement ───────────────────────
        if not job.remote:
            score -= 60

        # ── Payment method mentions ──────────────────────────────────
        if self._has_ethiopia_friendly_payment(text):
            score += 10
        elif self._has_restrictive_payment_indicator(text):
            score -= 30

        # ── Visa/citizenship requirements ────────────────────────────
        if self._requires_us_citizenship(text):
            score = 0

        # ── Sanctions check ─────────────────────────────────────────
        if self._country_is_sanctioned(job.country):
            score = 0

        return max(0, min(100, score))

    def is_eligible(self, job: Job) -> bool:
        """Return True if the job passes basic eligibility (score >= 50)."""
        return self.score(job) >= 50

    def _build_search_text(self, job: Job) -> str:
        """Combine all job text for search."""
        parts = [
            job.title,
            job.description or "",
            job.country or "",
            job.source,
            " ".join(job.tags),
            job.company,
        ]
        return " ".join(parts).lower()

    def _is_blocked_for_ethiopia(self, text: str) -> bool:
        """Check if posting explicitly excludes Ethiopia."""
        blocks = [
            "not available in ethiopia",
            "unavailable in ethiopia",
            "excluding ethiopia",
            "ethiopia not supported",
            "cannot pay ethiopia",
            "ethiopia restricted",
        ]
        return any(phrase in text for phrase in blocks)

    def _has_geographic_restriction(self, text: str) -> bool:
        """Detect geographic restrictions in listing."""
        return any(indicator in text for indicator in RESTRICTION_INDICATORS)

    def _has_ethiopia_friendly_payment(self, text: str) -> bool:
        """Detect payment methods usable in Ethiopia."""
        return any(pm in text for pm in ETHIOPIA_FRIENDLY_PAYMENT)

    def _has_restrictive_payment_indicator(self, text: str) -> bool:
        """Detect payment restrictions."""
        indicators = [
            "must have us bank account",
            "us bank account required",
            "ach transfer only",
            "direct deposit only",
            "must have ssn",
            "social security number required",
        ]
        return any(ind in text for ind in indicators)

    def _requires_us_citizenship(self, text: str) -> bool:
        """Check if posting explicitly requires US citizenship."""
        citizenship = [
            "us citizens only",
            "us citizenship required",
            "must be us citizen",
            "us person",
            "export controlled",
            "itear",
        ]
        return any(phrase in text for phrase in citizenship)

    def _country_is_sanctioned(self, country: Optional[str]) -> bool:
        """Check if the country is under sanctions that would block payments."""
        if not country:
            return False
        return country.lower().strip() in RESTRICTIVE_COUNTRIES
