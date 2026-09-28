"""
DEDAN Remote API tests.

Covers: job rendering, filters, search, pagination, save, application
tracking, API failures, authentication boundaries, external-redirect
validation, rate limiting, and response-shape stability.

Uses the real discovery database (read-only) plus an isolated temp user DB.
"""

from __future__ import annotations

import os
import tempfile

import pytest
from fastapi.testclient import TestClient

import api.store as store_module
from api.main import app
from api.security import rate_limiter, validate_external_url


@pytest.fixture()
def client():
    """TestClient with an isolated user store and reset rate limiter."""
    rate_limiter.reset()
    tmp = tempfile.mkdtemp(prefix="dedan_test_")
    store_module.reset_user_store_for_tests(os.path.join(tmp, "users.db"))
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture()
def auth_client(client):
    """Client registered + signed in; auth header applied to all requests."""
    resp = client.post(
        "/api/auth/register",
        json={"email": "qa@example.com", "password": "longenough1", "display_name": "QA"},
    )
    assert resp.status_code == 201, resp.text
    token = resp.json()["token"]
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


# ── Health & meta ────────────────────────────────────────────────────────────


class TestMeta:
    def test_health(self, client):
        r = client.get("/api/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_ready_reports_database_health(self, client):
        """Readiness must truthfully reflect whether the DB can be read."""
        r = client.get("/api/ready")
        data = r.json()
        ok = data["database"]["ok"]
        assert isinstance(ok, bool)
        assert r.status_code == (200 if ok else 503)
        assert data["status"] == ("ready" if ok else "not_ready")
        assert data["database"]["detail"]
        # The probe must never disclose internals.
        assert "Traceback" not in r.text

    def test_security_headers_on_every_response(self, client):
        r = client.get("/api/health")
        assert r.headers["X-Content-Type-Options"] == "nosniff"
        assert r.headers["X-Frame-Options"] == "DENY"
        assert r.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
        assert "Permissions-Policy" in r.headers
        # HSTS only when explicitly enabled, so plain-HTTP dev is not broken.
        assert "Strict-Transport-Security" not in r.headers

    def test_request_id_correlates_requests(self, client):
        assert client.get("/api/health").headers["X-Request-ID"]
        echoed = client.get("/api/health", headers={"X-Request-ID": "abc123"})
        assert echoed.headers["X-Request-ID"] == "abc123"

    def test_stats_truthful_shape(self, client):
        r = client.get("/api/stats")
        assert r.status_code == 200
        data = r.json()
        assert data["total_opportunities"] >= 0
        assert data["sources_monitored"] >= 0
        # Stats must never contain credential-shaped keys.
        blob = str(data).lower()
        for marker in ("password", "secret", "token", "api_key"):
            assert marker not in blob

    def test_status_no_simulated_activity(self, client):
        r = client.get("/api/status")
        assert r.status_code == 200
        data = r.json()
        assert data["engine"] in ("operational", "degraded", "idle")
        assert "last_discovery_label" in data

    def test_sources_monitored(self, client):
        r = client.get("/api/sources")
        assert r.status_code == 200
        items = r.json()
        assert isinstance(items, list)
        if items:
            assert {"id", "name", "job_count", "monitored"} <= set(items[0])

    def test_categories_from_real_tags(self, client):
        r = client.get("/api/categories")
        assert r.status_code == 200
        for item in r.json():
            assert item["count"] >= 1

    def test_system_requires_token(self, client):
        r = client.get("/api/system/overview")
        assert r.status_code in (401, 503)  # not configured OR unauthorized
        assert "error" in r.json()


# ── Jobs: rendering, search, filters, pagination ───────────────────────────


class TestJobsList:
    def test_list_returns_page_envelope(self, client):
        r = client.get("/api/jobs")
        assert r.status_code == 200
        data = r.json()
        assert {"items", "page", "page_size", "total", "pages", "has_next", "has_prev"} <= set(data)

    def test_job_card_fields(self, client):
        r = client.get("/api/jobs?page_size=5")
        items = r.json()["items"]
        assert items, "expected real jobs in discovery DB"
        job = items[0]
        required = [
            "id",
            "slug",
            "title",
            "company",
            "source",
            "source_info",
            "apply_url",
            "salary_disclosed",
            "location_label",
            "remote",
            "tags",
            "freshness",
            "score",
        ]
        for field in required:
            assert field in job, f"missing {field}"
        # Honest salary representation — never a fabricated number.
        if job["salary"] is None:
            assert job["salary_disclosed"] is False
        # Freshness must carry a human label.
        assert job["freshness"]["label"]

    def test_search_filters_results(self, client):
        all_items = client.get("/api/jobs", params={"page_size": 50}).json()
        total = all_items["total"]
        filtered = client.get(
            "/api/jobs",
            params={"q": "annotation", "page_size": 50},
        ).json()
        assert filtered["total"] <= total
        # Every hit must actually contain the term somewhere.
        for job in filtered["items"]:
            blob = " ".join(
                [
                    job["title"],
                    job["company"],
                    job["source"],
                    " ".join(job["tags"]),
                    job["description"] or "",
                ]
            ).lower()
            assert "annotation" in blob

    def test_source_filter(self, client):
        r = client.get("/api/jobs", params={"source": "oneforma", "page_size": 50})
        assert r.status_code == 200
        for job in r.json()["items"]:
            assert job["source"] == "oneforma"

    def test_min_score_filter(self, client):
        r = client.get("/api/jobs", params={"min_score": 65, "page_size": 50})
        assert r.status_code == 200
        for job in r.json()["items"]:
            assert job["score"] >= 65

    def test_remote_filter(self, client):
        r = client.get("/api/jobs", params={"remote": "true", "page_size": 50})
        assert r.status_code == 200
        for job in r.json()["items"]:
            assert job["remote"] is True

    @pytest.mark.parametrize("sort", ["newest", "best_match", "score", "salary", "freshness"])
    def test_all_sorts_accepted(self, client, sort):
        r = client.get("/api/jobs", params={"sort": sort})
        assert r.status_code == 200

    def test_score_sort_descending(self, client):
        items = client.get(
            "/api/jobs",
            params={"sort": "score", "page_size": 50},
        ).json()["items"]
        scores = [j["score"] for j in items]
        assert scores == sorted(scores, reverse=True)

    def test_pagination_bounds(self, client):
        r1 = client.get("/api/jobs", params={"page": 1, "page_size": 5})
        d1 = r1.json()
        assert d1["page"] == 1
        assert len(d1["items"]) <= 5
        if d1["total"] > 5:
            r2 = client.get("/api/jobs", params={"page": 2, "page_size": 5})
            d2 = r2.json()
            assert d2["has_prev"] is True
            ids1 = {j["id"] for j in d1["items"]}
            ids2 = {j["id"] for j in d2["items"]}
            assert not (ids1 & ids2), "pages must not overlap"

    def test_page_size_capped(self, client):
        r = client.get("/api/jobs", params={"page_size": 500})
        assert r.status_code == 422  # validation rejects > 50

    def test_invalid_page_rejected(self, client):
        r = client.get("/api/jobs", params={"page": 0})
        assert r.status_code == 422

    def test_empty_query_returns_valid_page(self, client):
        r = client.get("/api/jobs", params={"q": "zzz-no-such-job-zzz"})
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 0
        assert data["items"] == []


class TestJobDetail:
    def _first_job_id(self, client) -> str:
        items = client.get("/api/jobs").json()["items"]
        assert items, "discovery DB should contain at least one job"
        return items[0]["id"]

    def test_detail_ok(self, client):
        job_id = self._first_job_id(client)
        r = client.get(f"/api/jobs/{job_id}")
        assert r.status_code == 200
        data = r.json()
        assert data["id"] == job_id
        # Ranking explanation is always present on detail (system ranking).
        assert data["score_explanation"] is not None
        assert data["score_explanation"]["basis"] == "system_ranking"
        # Intelligence is clearly labeled as experimental/regional.
        if data["intelligence"] is not None:
            assert "Ethiopia" in data["intelligence_note"]

    def test_slug_lookup(self, client):
        job_id = self._first_job_id(client)
        slug = client.get(f"/api/jobs/{job_id}").json()["slug"]
        r = client.get(f"/api/jobs/{slug}")
        assert r.status_code == 200
        assert r.json()["id"] == job_id

    def test_unknown_job_404_structured(self, client):
        r = client.get("/api/jobs/ffffffffffffffff")
        assert r.status_code == 404
        err = r.json()["error"]
        assert err["code"] == "not_found"
        assert "Traceback" not in r.text

    def test_no_python_leak_on_missing_route(self, client):
        r = client.get("/api/definitely-missing")
        assert r.status_code == 404
        assert "error" in r.json()
        assert "Traceback" not in r.text
        assert "sqlite3" not in r.text


# ── Auth boundaries ─────────────────────────────────────────────────────────


class TestAuth:
    def test_register_login_me_logout(self, client):
        r = client.post(
            "/api/auth/register",
            json={"email": "user@example.com", "password": "longenough1"},
        )
        assert r.status_code == 201
        token = r.json()["token"]
        h = {"Authorization": f"Bearer {token}"}

        me = client.get("/api/auth/me", headers=h)
        assert me.status_code == 200
        assert me.json()["user"]["email"] == "user@example.com"
        # password hash must never appear in responses
        assert "password" not in me.text

        out = client.post("/api/auth/logout", headers=h)
        assert out.status_code == 200
        me2 = client.get("/api/auth/me", headers=h)
        assert me2.status_code == 401  # token revoked

    def test_duplicate_email_conflict(self, client):
        payload = {"email": "dup@example.com", "password": "longenough1"}
        assert client.post("/api/auth/register", json=payload).status_code == 201
        r = client.post("/api/auth/register", json=payload)
        assert r.status_code == 409

    def test_wrong_password_rejected(self, client):
        client.post(
            "/api/auth/register", json={"email": "x@example.com", "password": "longenough1"}
        )
        r = client.post(
            "/api/auth/login", json={"email": "x@example.com", "password": "wrongpass11"}
        )
        assert r.status_code == 401

    def test_short_password_validation(self, client):
        r = client.post("/api/auth/register", json={"email": "y@example.com", "password": "short"})
        assert r.status_code == 422

    def test_bogus_token_rejected(self, client):
        r = client.get("/api/saved", headers={"Authorization": "Bearer not-a-real-token"})
        assert r.status_code == 401

    def test_browse_requires_no_auth(self, client):
        # Public experience must never be gated.
        for path in ("/api/jobs", "/api/stats", "/api/status", "/api/sources", "/api/categories"):
            assert client.get(path).status_code == 200, path

    @pytest.mark.parametrize(
        "path", ["/api/saved", "/api/applications", "/api/profile", "/api/recommendations"]
    )
    def test_private_endpoints_require_auth(self, client, path):
        assert client.get(path).status_code == 401


# ── Save & application tracking ────────────────────────────────────────────


class TestSaved:
    def test_save_list_unsave_cycle(self, auth_client):
        job_id = auth_client.get("/api/jobs").json()["items"][0]["id"]

        r = auth_client.post(f"/api/jobs/{job_id}/save", json={"note": "follow up"})
        assert r.status_code == 201

        saved = auth_client.get("/api/saved").json()
        assert len(saved) == 1
        assert saved[0]["job"]["id"] == job_id
        assert saved[0]["note"] == "follow up"

        r = auth_client.delete(f"/api/jobs/{job_id}/save")
        assert r.status_code == 200
        assert auth_client.get("/api/saved").json() == []

    def test_unsave_missing_is_404(self, auth_client):
        job_id = auth_client.get("/api/jobs").json()["items"][0]["id"]
        r = auth_client.delete(f"/api/jobs/{job_id}/save")
        assert r.status_code == 404

    def test_save_requires_auth(self, client):
        job_id = client.get("/api/jobs").json()["items"][0]["id"]
        r = client.post(f"/api/jobs/{job_id}/save", json={})
        assert r.status_code == 401


class TestApplications:
    def test_full_tracking_lifecycle(self, auth_client):
        job_id = auth_client.get("/api/jobs").json()["items"][0]["id"]

        r = auth_client.post(
            "/api/applications",
            json={"job_id": job_id, "status": "application_started"},
        )
        assert r.status_code == 201
        app_id = r.json()["id"]

        for status_value in ("applied", "interview", "offer"):
            r = auth_client.patch(
                f"/api/applications/{app_id}",
                json={"status": status_value},
            )
            assert r.status_code == 200
            assert r.json()["status"] == status_value

        apps = auth_client.get("/api/applications").json()
        assert len(apps) == 1
        assert apps[0]["status"] == "offer"
        assert apps[0]["job"]["id"] == job_id

    def test_invalid_status_rejected(self, auth_client):
        job_id = auth_client.get("/api/jobs").json()["items"][0]["id"]
        r = auth_client.post(
            "/api/applications",
            json={"job_id": job_id, "status": "definitely-not-real"},
        )
        assert r.status_code == 422

    def test_application_for_unknown_job_404(self, auth_client):
        r = auth_client.post("/api/applications", json={"job_id": "ffffffffffffffff"})
        assert r.status_code == 404

    def test_patch_other_users_application_404(self, client):
        # user A creates
        r = client.post(
            "/api/auth/register", json={"email": "a@example.com", "password": "longenough1"}
        )
        h_a = {"Authorization": f"Bearer {r.json()['token']}"}
        job_id = client.get("/api/jobs").json()["items"][0]["id"]
        created = client.post(
            "/api/applications",
            json={"job_id": job_id},
            headers=h_a,
        ).json()

        # user B tries to patch it
        r = client.post(
            "/api/auth/register", json={"email": "b@example.com", "password": "longenough1"}
        )
        h_b = {"Authorization": f"Bearer {r.json()['token']}"}
        r = client.patch(
            f"/api/applications/{created['id']}", json={"status": "rejected"}, headers=h_b
        )
        assert r.status_code == 404

    def test_user_cannot_see_others_applications(self, client):
        r = client.post(
            "/api/auth/register", json={"email": "c@example.com", "password": "longenough1"}
        )
        h = {"Authorization": f"Bearer {r.json()['token']}"}
        assert client.get("/api/applications", headers=h).json() == []


# ── External redirect safety ────────────────────────────────────────────────


class TestExternalUrlValidation:
    @pytest.mark.parametrize(
        "url",
        [
            "javascript:alert(1)",
            "file:///etc/passwd",
            "data:text/html,<script>x</script>",
            "http://localhost/admin",
            "http://127.0.0.1/x",
            "http://169.254.169.254/latest/meta-data/",
            "http://192.168.1.1/router",
            "http://10.0.0.5/internal",
            "ftp://evil.example.com/payload",
            "",
        ],
    )
    def test_blocked_urls(self, url):
        valid, cleaned = validate_external_url(url)
        assert valid is False
        assert cleaned is None

    @pytest.mark.parametrize(
        "url",
        [
            "https://example.com/jobs/123",
            "http://jobs.telusdigital.com/?ref=abc",
            "https://outlier.ai/expert-jobs?q=x#top",
        ],
    )
    def test_allowed_urls(self, url):
        valid, cleaned = validate_external_url(url)
        assert valid is True
        assert cleaned.startswith(("http://", "https://"))

    def test_none_is_blocked(self):
        assert validate_external_url(None) == (False, None)

    def test_detail_apply_url_is_validated(self, client):
        job = client.get("/api/jobs").json()["items"][0]
        detail = client.get(f"/api/jobs/{job['id']}").json()
        # apply_url is either None (blocked) or a validated http(s) URL.
        if detail["apply_url"] is not None:
            valid, _ = validate_external_url(detail["apply_url"])
            assert valid


# ── Preferences & recommendations ──────────────────────────────────────────


class TestPreferences:
    def test_patch_and_read_profile(self, auth_client):
        r = auth_client.patch(
            "/api/profile",
            json={
                "display_name": "Ada",
                "categories": ["ai-ml", "data"],
                "experience": "intermediate",
                "regions": ["worldwide", "europe"],
            },
        )
        assert r.status_code == 200
        data = r.json()
        assert data["user"]["display_name"] == "Ada"
        assert data["preferences"]["categories"] == ["ai-ml", "data"]
        assert data["preferences"]["experience"] == "intermediate"

    def test_invalid_experience_rejected(self, auth_client):
        r = auth_client.patch("/api/profile", json={"experience": "wizard"})
        assert r.status_code == 422

    def test_recommendations_labeled_not_ai_claims(self, auth_client):
        auth_client.patch("/api/profile", json={"categories": ["data"], "regions": ["worldwide"]})
        r = auth_client.get("/api/recommendations")
        assert r.status_code == 200
        data = r.json()
        assert data["basis"] in ("user_preferences", "top_ranked")
        # Must be transparent about method — no fake AI claims.
        assert "AI language model" in data["method"] or "not" in data["method"].lower()
        for item in data["items"]:
            if item["preference_match"]:
                assert item["preference_match"]["basis"] == "user_preferences"
                assert "Not an AI prediction" in item["preference_match"]["note"]

    def test_recommendations_without_prefs_show_top_ranked(self, auth_client):
        r = auth_client.get("/api/recommendations")
        assert r.status_code == 200
        assert r.json()["basis"] == "top_ranked"


# ── Rate limiting ───────────────────────────────────────────────────────────


class TestRateLimit:
    def test_auth_endpoint_rate_limited(self, client):
        rate_limiter.reset()
        code = None
        for _ in range(15):
            r = client.post(
                "/api/auth/login",
                json={"email": "nobody@example.com", "password": "x"},
            )
            code = r.status_code
            if code == 429:
                break
        assert code == 429, "auth limiter should trigger within 15 attempts"
        assert r.json()["error"]["code"] == "rate_limited"
        # Regression: the structured-error handler must carry per-error headers
        # through, otherwise clients cannot back off politely.
        assert r.headers.get("Retry-After") == "60"
        assert int(r.headers["Retry-After"]) > 0


# ── Command search ──────────────────────────────────────────────────────────


class TestCommandSearch:
    """Search must group real results and never pad an empty query."""

    def test_empty_query_returns_empty_payload(self, client):
        r = client.get("/api/search", params={"q": ""})
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 0
        assert data["opportunities"] == []
        assert data["suggestions"] == []
        # It should still teach the user what to do.
        assert data["note"]

    def test_matches_real_opportunities(self, client):
        r = client.get("/api/search", params={"q": "ai", "limit": 3})
        assert r.status_code == 200
        data = r.json()
        assert data["query"] == "ai"
        assert data["total"] >= len(data["opportunities"])
        assert len(data["opportunities"]) <= 3
        for item in data["opportunities"]:
            # Every hit is a real serialized opportunity.
            for field in ("id", "slug", "title", "source_info", "freshness", "score"):
                assert field in item

    def test_no_match_is_honest(self, client):
        r = client.get("/api/search", params={"q": "zzzz-unfindable-query-zzzz"})
        assert r.status_code == 200
        data = r.json()
        assert data["opportunities"] == []
        assert data["total"] == 0

    def test_suggestions_are_runnable_kinds(self, client):
        r = client.get("/api/search", params={"q": "ai"})
        for suggestion in r.json()["suggestions"]:
            assert suggestion["kind"] in ("tag", "source", "category", "keyword")
            assert suggestion["label"]
        # The response must state that matching is deterministic, not magic.
        assert "not a language model" in r.json()["note"].lower()


# ── Activity signals ────────────────────────────────────────────────────────


class TestActivitySignals:
    def test_requires_authentication(self, client):
        assert client.get("/api/notifications").status_code == 401

    def test_empty_for_brand_new_user(self, auth_client):
        r = auth_client.get("/api/notifications")
        assert r.status_code == 200
        data = r.json()
        assert data["items"] == []
        # An empty feed explains how to create one instead of faking activity.
        assert "No recorded activity" in data["note"]

    def test_reflects_real_user_actions(self, auth_client):
        page = auth_client.get("/api/jobs", params={"page_size": 1}).json()
        if not page["items"]:
            pytest.skip("discovery database has no rows to act on")
        job_id = page["items"][0]["id"]

        assert auth_client.post(f"/api/jobs/{job_id}/save", json={}).status_code == 201
        assert (
            auth_client.post(
                "/api/applications",
                json={"job_id": job_id, "status": "applied"},
            ).status_code
            == 201
        )

        items = auth_client.get("/api/notifications").json()["items"]
        kinds = {item["kind"] for item in items}
        assert "saved" in kinds
        assert "application" in kinds

        for item in items:
            # Every signal carries a real timestamp, a job that exists, and a
            # kind the UI can categorise.
            assert item["at"]
            assert item["kind"] in ("saved", "application", "discovery")
            assert item["title"]
            assert item["job"] and item["job"]["id"]


# ── Versioned surface ───────────────────────────────────────────────────────


class TestVersionedAliases:
    """`/api` is canonical; `/api/v1` mirrors it for future breaking changes."""

    @pytest.mark.parametrize(
        "path",
        [
            "/api/v1/health",
            "/api/v1/stats",
            "/api/v1/sources",
            "/api/v1/categories",
            "/api/v1/jobs?page_size=2",
            "/api/v1/search?q=ai",
        ],
    )
    def test_public_aliases_respond(self, client, path):
        assert client.get(path).status_code == 200

    def test_authenticated_alias_shares_auth(self, auth_client):
        assert auth_client.get("/api/v1/profile").status_code == 200
        assert auth_client.get("/api/v1/applications").status_code == 200
        assert auth_client.get("/api/v1/saved").status_code == 200

    def test_alias_payload_matches_canonical(self, client):
        canonical = client.get("/api/categories").json()
        aliased = client.get("/api/v1/categories").json()
        assert canonical == aliased

    def test_alias_not_duplicated_in_schema(self, client):
        """Docs should list one canonical path per operation."""
        paths = client.get("/api/openapi.json").json()["paths"]
        assert not any(p.startswith("/api/v1") for p in paths)
        assert "/api/jobs" in paths
