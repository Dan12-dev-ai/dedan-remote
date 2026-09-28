"""
Standardized Job model used across all scrapers and agents.
Every scraper returns an instance of this dataclass.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass(frozen=True, slots=True)
class Job:
    """
    Immutable, standardized job representation.

    All scrapers must return this structure.
    The ``id`` field is a SHA-256 hash derived from the source + url (or title)
    to guarantee uniqueness across platforms.
    """

    title: str
    company: str
    url: str
    source: str  # e.g. "outlier", "alignerr", "oneforma"

    # Optional fields — will be normalised to None if empty.
    salary: Optional[str] = None
    country: Optional[str] = None
    remote: bool = True
    posted_date: Optional[str] = None
    description: Optional[str] = None
    tags: list[str] = field(default_factory=list)

    # ── internally computed ──────────────────────────────────────────────
    id: str = field(init=False, compare=True, hash=True)
    discovered_at: datetime = field(
        init=False,
        default_factory=lambda: datetime.now(timezone.utc),
    )

    def __post_init__(self) -> None:
        """Compute a deterministic unique id from source + url."""
        raw = f"{self.source}:{self.url}".encode("utf-8")
        object.__setattr__(self, "id", hashlib.sha256(raw).hexdigest()[:16])

    @property
    def apply_url(self) -> str:
        """Return the application URL (alias for url)."""
        return self.url

    @property
    def is_remote(self) -> bool:
        """Convenience property."""
        return self.remote

    @property
    def short_summary(self) -> str:
        """Generate a one-line summary for notification bodies."""
        loc = "🌍 Worldwide" if not self.country else f"📍 {self.country}"
        tags_str = ", ".join(self.tags[:5]) if self.tags else "N/A"
        return (
            f"{self.title} @ {self.company} | {loc} | 💰 {self.salary or 'N/A'} | Tags: {tags_str}"
        )

    def to_dict(self) -> dict[str, object]:
        """Serialize to dict for database storage."""
        return {
            "id": self.id,
            "title": self.title,
            "company": self.company,
            "url": self.url,
            "source": self.source,
            "salary": self.salary,
            "country": self.country,
            "remote": int(self.remote),
            "posted_date": self.posted_date,
            "description": self.description,
            "tags": ",".join(self.tags),
            "discovered_at": self.discovered_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> Job:
        """Rehydrate a Job from a dict (e.g. from DB row)."""
        tags_str = data.get("tags") or ""
        tags = tags_str.split(",") if isinstance(tags_str, str) and tags_str else []
        return cls(
            title=str(data["title"]),
            company=str(data["company"]),
            url=str(data["url"]),
            source=str(data["source"]),
            salary=str(data["salary"]) if data.get("salary") else None,
            country=str(data["country"]) if data.get("country") else None,
            remote=bool(int(str(data.get("remote", "1")))),
            posted_date=str(data["posted_date"]) if data.get("posted_date") else None,
            description=str(data["description"]) if data.get("description") else None,
            tags=tags,
        )


def job_from_scraper_result(
    title: str,
    company: str,
    url: str,
    source: str,
    salary: Optional[str] = None,
    country: Optional[str] = None,
    remote: bool = True,
    posted_date: Optional[str] = None,
    description: Optional[str] = None,
    tags: Optional[list[str]] = None,
) -> Job:
    """Factory function to create a Job from scraper result."""
    return Job(
        title=title.strip(),
        company=company.strip(),
        url=url.strip(),
        source=source.strip().lower(),
        salary=salary.strip() if salary else None,
        country=country.strip() if country else None,
        remote=remote,
        posted_date=posted_date.strip() if posted_date else None,
        description=description.strip() if description else None,
        tags=[t.strip().lower() for t in tags] if tags else [],
    )
