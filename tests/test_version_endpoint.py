"""Build identity endpoint.

A transparency page has to state the running version without anyone remembering
to update it, so `/api/version` derives every field from the app definition and
the generated OpenAPI document instead of a hand-edited string.
"""

from __future__ import annotations

import pytest

from api.main import create_app


@pytest.fixture()
def client():
    from api.security import rate_limiter

    rate_limiter.reset()
    from fastapi.testclient import TestClient

    with TestClient(create_app(), raise_server_exceptions=False) as c:
        yield c


class TestVersionEndpoint:
    def test_reports_the_running_build(self, client):
        r = client.get("/api/version")
        assert r.status_code == 200
        body = r.json()
        assert body["service"] == "dedan-remote"
        # Must match the app definition, not a literal in this test.
        assert body["version"] == create_app().version

    def test_openapi_version_and_path_count_match_the_served_schema(self, client):
        body = client.get("/api/version").json()
        schema = client.get("/api/openapi.json").json()
        assert body["openapi_version"] == schema["openapi"]
        assert body["schema_paths"] == len(schema["paths"])
        assert body["schema_paths"] > 0

    def test_points_at_the_interactive_docs(self, client):
        body = client.get("/api/version").json()
        assert body["docs_url"] == "/api/docs"
        assert client.get(body["docs_url"]).status_code == 200

    def test_requires_no_authentication(self, client):
        """It is public on purpose — a status page cannot ask you to sign in."""
        assert client.get("/api/version").status_code == 200

    def test_versioned_alias_serves_the_same_payload(self, client):
        assert client.get("/api/v1/version").json() == client.get("/api/version").json()

    def test_shape_is_exactly_the_declared_contract(self, client):
        body = client.get("/api/version").json()
        assert set(body) == {
            "service",
            "version",
            "openapi_version",
            "docs_url",
            "schema_paths",
        }

    def test_does_not_leak_internals(self, client):
        """No host paths, tokens or settings leak through a public endpoint."""
        body = client.get("/api/version").json()
        blob = " ".join(str(v) for v in body.values()).lower()
        for leak in ("token", "secret", "password", "database", "/home", ".env"):
            assert leak not in blob, f"{leak!r} leaked into /api/version"