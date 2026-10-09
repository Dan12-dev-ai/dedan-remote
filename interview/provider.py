"""
LLM provider — one small adapter over any OpenAI-compatible chat endpoint.

Why an adapter instead of a vendor SDK: the DEDAN interview engine only needs
`POST {base}/chat/completions` returning JSON. That single shape covers OpenAI,
Groq, Together, OpenRouter, vLLM, llama.cpp and a local Ollama server, so
swapping providers is an environment change, not a code change.

Hard rule enforced here: if no provider is configured, we raise
`ProviderUnavailable` — we never fall back to a canned response. A fabricated
interview evaluation would be worse than no interview.
"""

from __future__ import annotations

import json
import logging
import random
import re
import time
from datetime import datetime, timezone
from typing import Any, Optional

import requests

from config.settings import Settings, get_settings

try:  # stdlib, but kept defensive so a stripped runtime still imports
    from email.utils import parsedate_to_datetime
except ImportError:  # pragma: no cover
    parsedate_to_datetime = None  # type: ignore[assignment]

logger = logging.getLogger("dedan.interview.llm")


class ProviderUnavailable(RuntimeError):
    """No usable model — the caller must surface 'not configured', not guess."""


class ProviderError(RuntimeError):
    """The provider was reachable but failed. Message is safe to log."""


# ── retry policy ────────────────────────────────────────────────────────────
#
# Free-tier inference endpoints (Groq, Together, OpenRouter, local Ollama behind
# a busy LAN) fail transiently far more often than paid ones: 429s during peak
# windows, 502s from a cold worker, connection resets on a long completion.
# Retrying with exponential backoff plus jitter is the difference between a
# feature that works and one that intermittently 500s.
#
# Only *retryable* conditions are retried. A 401 or a 400 means the request is
# wrong and retrying it just burns quota — those fail fast.

RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 529})


class RetryBudget:
    """
    Exponential backoff with full jitter, bounded by attempts and a deadline.

    ``full jitter`` (``random.uniform(0, backoff)``) rather than fixed
    backoff: when a free tier starts rejecting, every client that backs off in
    lockstep retries in lockstep and re-creates the thundering herd. Jitter
    spreads them.

    The deadline matters more than the attempt count for a synchronous HTTP
    handler: four retries against a 30s per-attempt timeout would hold a
    request open for two minutes and trip the gateway. Whichever bound binds
    first wins.
    """

    def __init__(
        self,
        *,
        attempts: int = 4,
        base_delay: float = 0.6,
        cap_delay: float = 8.0,
        deadline: float = 45.0,
        rng: Optional[random.Random] = None,
        sleep: Optional[Any] = None,
    ) -> None:
        self.attempts = max(1, attempts)
        self.base_delay = base_delay
        self.cap_delay = cap_delay
        self.deadline = deadline
        self._rng = rng or random.Random()
        self._sleep = sleep or time.sleep
        self._started = time.monotonic()
        self.attempts_made = 0
        self.slept_seconds = 0.0

    @property
    def remaining(self) -> float:
        return max(0.0, self.deadline - (time.monotonic() - self._started))

    def should_retry(self, *, retryable: bool, attempt: int) -> bool:
        if not retryable or attempt >= self.attempts:
            return False
        # Leave a slice of the budget for the final attempt to actually run.
        return self.remaining > self.base_delay

    def delay_for(self, attempt: int, retry_after: Optional[float] = None) -> float:
        """Wait before ``attempt`` (1-based), never exceeding the deadline."""
        if retry_after is not None and retry_after >= 0:
            wait = min(float(retry_after), self.cap_delay)
        else:
            exponential = self.base_delay * (2 ** (attempt - 1))
            wait = self._rng.uniform(0, min(exponential, self.cap_delay))
        # Never sleep past what is left, minus a small floor to act on.
        return max(0.0, min(wait, self.remaining - 0.25))

    def wait(self, attempt: int, retry_after: Optional[float] = None) -> float:
        seconds = self.delay_for(attempt, retry_after)
        if seconds > 0:
            self._sleep(seconds)
            self.slept_seconds += seconds
        return seconds


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def _retry_after_seconds(header: Optional[str]) -> Optional[float]:
    """
    Honour ``Retry-After`` when the provider sends it.

    Both forms are allowed: delay-seconds and an HTTP date. Free tiers lean on
    the header heavily, and ignoring it means retrying straight into the next
    rate-limit window.
    """
    if not header:
        return None
    raw = header.strip()
    try:
        seconds = float(raw)
    except ValueError:
        try:
            target = parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            return None
        if target.tzinfo is None:
            target = target.replace(tzinfo=timezone.utc)
        seconds = (target - datetime.now(timezone.utc)).total_seconds()
    return max(0.0, seconds)


def _extract_json(raw: str) -> dict[str, Any]:
    """
    Pull one JSON object out of a model reply.

    Models wrap JSON in prose or code fences often enough that a strict
    `json.loads` on the whole string is not worth the failure rate.
    """
    text = raw.strip()
    fence = _FENCE.search(text)
    if fence:
        text = fence.group(1).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end <= start:
            raise ProviderError("model reply contained no JSON object")
        try:
            parsed = json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ProviderError(f"model reply was not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ProviderError("model reply was not a JSON object")
    return parsed


class LLMClient:
    """Thin, synchronous client for one configured chat model."""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self._settings = settings or get_settings()

    # ── capability ──────────────────────────────────────────────────────────

    @property
    def available(self) -> bool:
        return self._settings.interview_configured

    @property
    def unavailable_reason(self) -> str:
        return self._settings.interview_unavailable_reason

    def _require(self) -> tuple[str, str]:
        if not self.available:
            raise ProviderUnavailable(self.unavailable_reason or "Interview model not configured.")
        base = self._settings.llm_endpoint
        key = self._settings.LLM_API_KEY.strip()
        # Local servers (Ollama/llama.cpp) ignore the key but the header is
        # still expected by some builds.
        return f"{base}/chat/completions", key or "not-needed"

    # ── call ────────────────────────────────────────────────────────────────

    def complete(
        self,
        *,
        system: str,
        user: str,
        temperature: Optional[float] = None,
        max_tokens: int = 1400,
    ) -> str:
        """One chat completion, returned as raw text."""
        url, key = self._require()
        payload: dict[str, Any] = {
            "model": self._settings.LLM_MODEL,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": (self._settings.LLM_TEMPERATURE if temperature is None else temperature),
            "max_tokens": max_tokens,
        }
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }

        budget = RetryBudget(
            attempts=self._settings.LLM_MAX_ATTEMPTS,
            base_delay=self._settings.LLM_RETRY_BASE_DELAY_SECONDS,
            cap_delay=self._settings.LLM_RETRY_MAX_DELAY_SECONDS,
            deadline=self._settings.LLM_RETRY_DEADLINE_SECONDS,
        )

        response = None
        last_error = ""
        for attempt in range(1, budget.attempts + 1):
            budget.attempts_made = attempt
            retry_after: Optional[float] = None

            try:
                response = requests.post(
                    url,
                    json=payload,
                    headers=headers,
                    timeout=self._settings.LLM_TIMEOUT_SECONDS,
                )
            except requests.Timeout:
                # The canonical retryable case: a long completion outran the
                # per-attempt timeout.
                last_error = f"timed out after {self._settings.LLM_TIMEOUT_SECONDS}s"
            except requests.RequestException as exc:
                # Connection reset, DNS blip, TLS hiccup.
                last_error = str(exc)[:200].replace(key, "***")
            else:
                if response.status_code < 400:
                    break
                last_error = (
                    f"HTTP {response.status_code}: {response.text[:300].replace(key, '***')}"
                )
                if response.status_code not in RETRYABLE_STATUS:
                    # Auth failure or a malformed request. Retrying burns quota
                    # and will fail identically, so surface it immediately.
                    logger.warning("interview model %s", last_error)
                    raise ProviderError(f"interview model returned {last_error}")
                retry_after = _retry_after_seconds(response.headers.get("Retry-After"))
                logger.warning(
                    "interview model %s (attempt %s/%s)",
                    last_error,
                    attempt,
                    budget.attempts,
                )

            if not budget.should_retry(retryable=True, attempt=attempt):
                break
            budget.wait(attempt, retry_after)
        else:
            # Exhausted the attempt list without a successful response.
            response = None

        if response is None or response.status_code >= 400:
            raise ProviderError(
                f"interview model unavailable after {budget.attempts_made} "
                f"attempt(s): {last_error or 'no response'}"
            )

        try:
            body = response.json()
        except ValueError as exc:
            raise ProviderError("interview model returned a non-JSON response") from exc

        try:
            return body["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError("interview model response had no message content") from exc

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        temperature: Optional[float] = None,
        max_tokens: int = 1400,
    ) -> dict[str, Any]:
        """One chat completion, parsed into a JSON object."""
        raw = self.complete(
            system=system,
            user=user,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        if not raw.strip():
            raise ProviderError("interview model returned an empty reply")
        return _extract_json(raw)


_client: Optional[LLMClient] = None


def get_llm() -> LLMClient:
    """Process-wide client. Cheap to construct, but keeps config reads rare."""
    global _client
    if _client is None:
        _client = LLMClient()
    return _client


def reset_llm() -> None:
    """Drop the cached client (used by tests that patch settings)."""
    global _client
    _client = None
