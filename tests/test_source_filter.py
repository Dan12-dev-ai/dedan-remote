"""
Source and category filter semantics.

The explorer sends one query parameter per selected facet, so `?source=`
and `?category=` must behave exactly like `?country=` already does:
repeated values widen (OR) the result set.

`source` did not. It was declared as `Optional[str]` and matched with
`lower(source) = lower(?)`, so a repeated parameter collapsed to its first
value. Selecting TELUS and OneForma returned only OneForma's rows — the
second selection was silently discarded and the result count understated
the feed. These tests lock the union behaviour so it cannot regress.
"""

from __future__ import annotations

import os
import tempfile

import pytest
from fastapi.testclient import TestClient

import api.store as store_module
from api.main import app
from api.security import rate_limiter
from database.database import Database
from models.job import Job

# Two sources with deliberately different row counts, so "did the second
# value get dropped?" has an unambiguous answer.
SOURCES = {"telus": 1, "oneforma": 13}


@pytest.fixture()
def db_path(monkeypatch):
    """A throwaway database seeded with a known row count per source."""
    tmp = tempfile.mkdtemp(prefix="dedan_source_")
    path = os.path.join(tmp, "jobs.db")
    monkeypatch.setenv("DATABASE_PATH", path)

    db = Database(path)
    db.create_tables()
    for source, count in SOURCES.items():
        for index in range(count):
            db.insert_job(
                Job(
                    title=f"Role {source} {index}",
                    company="TestCorp",
                    url=f"https://example.com/{source}/{index}",
                    source=source,
                    salary=None,
                    country=None,
                    remote=True,
                    posted_date="2026-09-01",
                    description="Remote AI annotation work.",
                    tags=["ai", "annotation"],
                ),
                70.0,
            )
    db.close()
    return path


@pytest.fixture()
def client(db_path):
    rate_limiter.reset()
    tmp = tempfile.mkdtemp(prefix="dedan_source_users_")
    store_module.reset_user_store_for_tests(os.path.join(tmp, "users.db"))
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def sources_of(payload: dict) -> set[str]:
    return {item["source"] for item in payload["items"]}


class TestSingleSourceStillWorks:
    def test_a_single_source_filters_to_itself(self, client):
        payload = client.get("/api/jobs", params={"source": "telus", "page_size": 50}).json()
        assert payload["total"] == SOURCES["telus"]
        assert sources_of(payload) == {"telus"}

    def test_source_matching_is_case_insensitive(self, client):
        payload = client.get("/api/jobs", params={"source": "TELUS", "page_size": 50}).json()
        assert payload["total"] == SOURCES["telus"]

    def test_unknown_source_returns_an_empty_page_not_an_error(self, client):
        payload = client.get("/api/jobs", params={"source": "nope", "page_size": 50}).json()
        assert payload["total"] == 0
        assert payload["items"] == []


class TestRepeatedSourceParams:
    def test_repeats_return_the_union_of_both_sources(self, client):
        """
        The regression this file exists for: the union must equal the sum,
        not just the first source's rows.
        """
        payload = client.get(
            "/api/jobs",
            params=[("source", "telus"), ("source", "oneforma"), ("page_size", 50)],
        ).json()
        assert payload["total"] == sum(SOURCES.values())
        assert sources_of(payload) == {"telus", "oneforma"}

    def test_every_item_of_each_source_is_present(self, client):
        payload = client.get(
            "/api/jobs",
            params=[("source", "telus"), ("source", "oneforma"), ("page_size", 50)],
        ).json()
        assert len(payload["items"]) == sum(SOURCES.values())

    def test_repeats_produce_no_duplicates(self, client):
        payload = client.get(
            "/api/jobs",
            params=[("source", "telus"), ("source", "oneforma"), ("page_size", 50)],
        ).json()
        ids = [item["id"] for item in payload["items"]]
        assert len(ids) == len(set(ids))

    def test_repeats_widen_rather_than_intersect(self, client):
        one = client.get("/api/jobs", params={"source": "oneforma"}).json()["total"]
        two = client.get(
            "/api/jobs",
            params=[("source", "telus"), ("source", "oneforma")],
        ).json()["total"]
        assert two > one

    def test_an_unknown_source_in_the_list_does_not_erase_the_known_ones(self, client):
        payload = client.get(
            "/api/jobs",
            params=[("source", "nope"), ("source", "telus"), ("page_size", 50)],
        ).json()
        assert payload["total"] == SOURCES["telus"]
        assert sources_of(payload) == {"telus"}

    def test_repeats_combine_with_keyword_search(self, client):
        payload = client.get(
            "/api/jobs",
            params=[("source", "telus"), ("source", "oneforma"), ("q", "Role telus"), ("page_size", 50)],
        ).json()
        # Search narrows the union rather than replacing the source filter.
        assert payload["total"] == SOURCES["telus"]
        assert sources_of(payload) == {"telus"}


class TestRepeatedCategoryParams:
    def test_repeated_categories_widen_the_result_set(self, client):
        single = client.get("/api/jobs", params={"category": "annotation", "page_size": 50}).json()["total"]
        pair = client.get(
            "/api/jobs",
            params=[("category", "annotation"), ("category", "microtask"), ("page_size", 50)],
        ).json()["total"]
        assert pair >= single
        assert pair == sum(SOURCES.values())

    def test_a_single_category_still_filters(self, client):
        payload = client.get("/api/jobs", params={"category": "annotation", "page_size": 50}).json()
        assert payload["total"] == sum(SOURCES.values())

    def test_an_unknown_category_returns_an_empty_page_not_an_error(self, client):
        payload = client.get("/api/jobs", params={"category": "quantum-basketweaving"}).json()
        assert payload["total"] == 0


class TestPaginationWithSourceFilter:
    def test_total_reflects_the_filter_not_the_page(self, client):
        payload = client.get(
            "/api/jobs",
            params=[("source", "oneforma"), ("page", 2), ("page_size", 5)],
        ).json()
        assert payload["total"] == SOURCES["oneforma"]
        assert payload["page"] == 2
        assert len(payload["items"]) == 5

    def test_the_union_paginates_without_losing_or_repeating_rows(self, client):
        seen = []
        for page in (1, 2):
            payload = client.get(
                "/api/jobs",
                params=[
                    ("source", "telus"),
                    ("source", "oneforma"),
                    ("page", page),
                    ("page_size", 10),
                ],
            ).json()
            seen.extend(item["id"] for item in payload["items"])
        assert len(seen) == len(set(seen))
        assert len(seen) == sum(SOURCES.values())
