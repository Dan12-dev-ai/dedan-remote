#!/usr/bin/env python3
"""Seed a small, self-contained demo opportunity set into the discovery DB.

Used by the deploy-review workflow so a public review instance has real rows to
render without running the live scrapers (a review instance must not scrape
third-party sites). It writes through the engine's own ``Database.insert_job``
API, so the data shape is identical to what the discovery pipeline produces.

Idempotent: ``insert_job`` skips jobs whose deterministic id already exists, so
re-running this never creates duplicates.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from database.database import Database
from models.job import job_from_scraper_result
from agents.ranking_agent import RankingAgent

# Representative listings across the nine real monitored sources. These are
# illustrative demo rows for review purposes only — clearly labelled in the UI
# via the "verify before applying" freshness badge the frontend already shows.
DEMO: list[dict[str, object]] = [
    {
        "title": "Data Annotation Specialist — Amharic",
        "company": "OneForma",
        "url": "https://www.oneforma.com/jobs/amharic-annotation",
        "source": "oneforma",
        "salary": "$18/hr",
        "country": "Ethiopia",
        "tags": ["ai", "annotation", "amharic", "remote"],
        "description": "Annotate Amharic text and audio samples to improve speech and language models.",
    },
    {
        "title": "Search Quality Rater",
        "company": "Appen",
        "url": "https://www.appen.com/jobs/search-quality-rater",
        "source": "appen",
        "salary": "$15/hr",
        "tags": ["ai", "evaluation", "search", "remote"],
        "description": "Evaluate search result relevance for a major search engine.",
    },
    {
        "title": "Microtask Contributor",
        "company": "Clickworker",
        "url": "https://www.clickworker.com/jobs/microtask",
        "source": "clickworker",
        "salary": "$10/hr",
        "tags": ["microtask", "data", "remote"],
        "description": "Complete short data-validation and categorization tasks.",
    },
    {
        "title": "AI Trainer — Swahili",
        "company": "TELUS Digital",
        "url": "https://jobs.telusdigital.com/ai-trainer-swahili",
        "source": "telus",
        "tags": ["ai", "training", "swahili", "remote"],
        "description": "Help train and evaluate conversational AI models in Swahili.",
    },
    {
        "title": "Coding Expert — Python",
        "company": "Outlier",
        "url": "https://outlier.ai/jobs/coding-expert-python",
        "source": "outlier",
        "salary": "$30/hr",
        "tags": ["ai", "coding", "python", "remote"],
        "description": "Write and critique Python solutions to train code-generation models.",
    },
    {
        "title": "Linguistic Data Collector",
        "company": "Alignerr",
        "url": "https://alignerr.com/careers/linguistic-data-collector",
        "source": "alignerr",
        "tags": ["ai", "linguistics", "data", "remote"],
        "description": "Collect and label linguistic data for model evaluation.",
    },
    {
        "title": "Localization Tester — French",
        "company": "Welocalize",
        "url": "https://www.welocalize.com/jobs/localization-tester-french",
        "source": "welocalize",
        "tags": ["localization", "testing", "french", "remote"],
        "description": "Test localized software builds for French-language accuracy.",
    },
    {
        "title": "Bounding Box Annotator",
        "company": "DataAnnotation",
        "url": "https://www.dataannotation.tech/jobs/bounding-box",
        "source": "dataannotation",
        "salary": "$20/hr",
        "tags": ["ai", "annotation", "vision", "remote"],
        "description": "Draw bounding boxes around objects in images to train vision models.",
    },
    {
        "title": "Relevance Assessor",
        "company": "Toloka",
        "url": "https://toloka.ai/jobs/relevance-assessor",
        "source": "toloka",
        "tags": ["ai", "assessment", "search", "remote"],
        "description": "Assess the relevance of search results for query-result pairs.",
    },
]


def main() -> int:
    db = Database()
    db.create_tables()
    ranker = RankingAgent()

    now = datetime.now(timezone.utc)
    inserted = 0
    for offset, spec in enumerate(DEMO):
        posted = (now - timedelta(days=offset + 1)).date().isoformat()
        job = job_from_scraper_result(
            title=str(spec["title"]),
            company=str(spec["company"]),
            url=str(spec["url"]),
            source=str(spec["source"]),
            salary=spec.get("salary"),  # type: ignore[arg-type]
            country=spec.get("country"),  # type: ignore[arg-type]
            remote=True,
            posted_date=posted,
            description=spec.get("description"),  # type: ignore[arg-type]
            tags=list(spec.get("tags", [])),  # type: ignore[arg-type]
        )
        score = float(ranker.score(job))
        if db.insert_job(job, score=score):
            inserted += 1

    db.close()
    print(f"seed_demo: inserted={inserted} total={len(DEMO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
