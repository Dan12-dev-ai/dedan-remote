"""
Country-filter and freshness-label semantics.

These lock two decisions that the UI depends on:

1. ``?country=`` may be repeated, and repeats widen (OR) the result set, so a
   multi-country explorer chip list returns the union rather than nothing.
2. ``freshness.label`` is a bare timeframe. Staleness is carried by the separate
   ``is_stale`` flag, never welded onto the label as text — otherwise every card
   has to strip a sentence out of a data field before it can be displayed.
"""

from __future__ import annotations

import os
import tempfile

import pytest
from fastapi.testclient import TestClient

import api.services as services
import api.store as store_module
from api.main import app
from api.security import rate_limiter
from database.database import Database
from models.job import Job

COUNTRIES = ["Ethiopia", "Germany", "Spain", "Kenya"]


@pytest.fixture()
def db_path(monkeypatch):
    """Point the discovery database at a throwaway file seeded per country."""
    tmp = tempfile.mkdtemp(prefix="dedan_country_")
    path = os.path.join(tmp, "jobs.db")
    # `get_settings()` re-reads the environment on every call, so setting the
    # variable is enough to redirect `connect_jobs_db`.
    monkeypatch.setenv("DATABASE_PATH", path)

    db = Database(path)
    db.create_tables()
    for index, country in enumerate(COUNTRIES):
        db.insert_job(
            Job(
                title=f"AI Task {index}",
                company="TestCorp",
                url=f"https://example.com/{index}",
                source="outlier",
                salary=None,
                country=country,
                remote=True,
                posted_date="2026-09-01",
                description=f"Remote AI work based in {country}.",
                tags=["ai"],
            ),
            70.0,
        )
    db.close()
    return path


@pytest.fixture()
def client(db_path):
    rate_limiter.reset()
    tmp = tempfile.mkdtemp(prefix="dedan_country_users_")
    store_module.reset_user_store_for_tests(os.path.join(tmp, "users.db"))
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def countries_of(payload: dict) -> set[str]:
    return {item["country"] for item in payload["items"]}


class TestMultiCountryFilter:
    def test_single_country_still_matches_a_substring(self, client):
        payload = client.get("/api/jobs", params={"country": "Ethiopia"}).json()
        assert countries_of(payload) == {"Ethiopia"}

    def test_repeated_country_params_return_the_union(self, client):
        payload = client.get(
            "/api/jobs",
            params=[("country", "Ethiopia"), ("country", "Spain")],
        ).json()
        assert countries_of(payload) == {"Ethiopia", "Spain"}

    def test_repeats_widen_rather_than_intersect(self, client):
        """Three selected countries must return more, never fewer, than one."""
        one = client.get("/api/jobs", params={"country": "Germany"}).json()["total"]
        three = client.get(
            "/api/jobs",
            params=[
                ("country", "Germany"),
                ("country", "Spain"),
                ("country", "Kenya"),
            ],
        ).json()["total"]
        assert three > one
        assert three == 3

    def test_unknown_country_returns_an_empty_page_not_an_error(self, client):
        payload = client.get("/api/jobs", params={"country": "Atlantis"}).json()
        assert payload["total"] == 0
        assert payload["items"] == []

    def test_services_layer_accepts_a_bare_string_and_a_sequence(self, db_path):
        as_string = services.query_jobs(country="Spain", page_size=50)
        as_list = services.query_jobs(country=["Spain"], page_size=50)
        assert as_string["total"] == as_list["total"] == 1

    def test_blank_entries_are_ignored_not_matched_as_empty(self, client):
        payload = client.get(
            "/api/jobs",
            params=[("country", "  "), ("country", "Kenya")],
        ).json()
        # An empty value must not become "match every row".
        assert countries_of(payload) == {"Kenya"}

    def test_multi_country_combines_with_the_other_filters(self, client):
        payload = client.get(
            "/api/jobs",
            params=[
                ("country", "Ethiopia"),
                ("country", "Germany"),
                ("min_score", "60"),
                ("page_size", "50"),
            ],
        ).json()
        assert countries_of(payload) <= {"Ethiopia", "Germany"}
        assert all(item["score"] >= 60 for item in payload["items"])


class TestFreshnessLabel:
    def test_label_is_a_bare_timeframe_without_a_stale_prefix(self, client):
        item = client.get("/api/jobs", params={"country": "Ethiopia"}).json()["items"][0]
        label = item["freshness"]["label"]
        assert not label.startswith("Potentially stale")
        assert "·" not in label

    def test_staleness_is_reported_as_a_flag_not_as_words(self, client):
        item = client.get("/api/jobs", params={"country": "Ethiopia"}).json()["items"][0]
        freshness = item["freshness"]
        # The seeded rows are dated 2026-09-01, well past the staleness window,
        # so the flag must be set while the label stays clean.
        assert freshness["is_stale"] is True
        assert freshness["label"].endswith("days ago")

    def test_a_fresh_row_is_not_flagged_stale(self):
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        fresh = services.freshness_info(
            {"posted_date": (now - timedelta(hours=2)).isoformat()}
        )
        assert fresh["is_stale"] is False
        assert fresh["label"] == "2h ago"

    def test_unknown_timestamp_still_labels_honestly(self):
        unknown = services.freshness_info({"posted_date": None, "discovered_at": None})
        assert unknown["label"] == "Unknown"
        assert unknown["is_stale"] is False
        assert unknown["age_days"] is None