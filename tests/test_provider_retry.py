import random
import pytest
import requests
import interview.provider as prov
from config.settings import Settings


@pytest.fixture()
def client(monkeypatch):
    cfg = Settings(INTERVIEW_ENABLED=True, LLM_API_KEY="k", LLM_MODEL="m",
                   LLM_MAX_ATTEMPTS=4, LLM_RETRY_BASE_DELAY_SECONDS=0.0,
                   LLM_RETRY_MAX_DELAY_SECONDS=0.0, LLM_RETRY_DEADLINE_SECONDS=10)
    monkeypatch.setattr(prov, "get_settings", lambda: cfg)
    monkeypatch.setattr(prov.time, "sleep", lambda s: None)
    prov.reset_llm()
    return prov.LLMClient(cfg)


class FakeResponse:
    """Minimal stand-in for `requests.Response`."""

    def __init__(self, status_code=200, headers=None, text="{}"):
        self.status_code = status_code
        self.headers = headers or {}
        self.text = text

    def json(self):
        return {"choices": [{"message": {"content": "hi"}}]}


def resp(code=200, headers=None, text="{}"):
    return FakeResponse(code, headers, text)


def test_recovers_after_transient_timeouts(client, monkeypatch):
    calls = {"n": 0}
    def post(*a, **k):
        calls["n"] += 1
        if calls["n"] < 3:
            raise requests.Timeout("slow")
        return resp(200)
    monkeypatch.setattr(prov.requests, "post", post)
    assert client.complete(system="s", user="u") == "hi"
    assert calls["n"] == 3


def test_does_not_retry_a_401(client, monkeypatch):
    calls = {"n": 0}
    def post(*a, **k):
        calls["n"] += 1
        return resp(401)
    monkeypatch.setattr(prov.requests, "post", post)
    with pytest.raises(prov.ProviderError, match="401"):
        client.complete(system="s", user="u")
    assert calls["n"] == 1, "retried a non-retryable status"


def test_gives_up_after_max_attempts(client, monkeypatch):
    calls = {"n": 0}
    def post(*a, **k):
        calls["n"] += 1
        return resp(503)
    monkeypatch.setattr(prov.requests, "post", post)
    with pytest.raises(prov.ProviderError, match="4 attempt"):
        client.complete(system="s", user="u")
    assert calls["n"] == 4


def test_scrubs_the_api_key_from_errors(client, monkeypatch):
    def post(*a, **k):
        class R:
            status_code = 500
            headers = {}
            text = '{"error":"bad key sk-secret123"}'
        return R()
    monkeypatch.setattr(prov.requests, "post", post)
    with pytest.raises(prov.ProviderError) as e:
        client.complete(system="s", user="u")
    assert "sk-secret123" not in str(e.value)


def test_honours_retry_after(client, monkeypatch):
    calls = {"n": 0}
    def post(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            return resp(429, headers={"Retry-After": "0"})
        return resp(200)
    monkeypatch.setattr(prov.requests, "post", post)
    assert client.complete(system="s", user="u") == "hi"
    assert calls["n"] == 2
