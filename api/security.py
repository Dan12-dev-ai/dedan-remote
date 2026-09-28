"""Security helpers: password hashing, tokens, URL validation, rate limiting."""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import re
import secrets
import time
from collections import defaultdict, deque
from typing import Any, Optional
from urllib.parse import urlparse

import bcrypt

# ── Passwords ────────────────────────────────────────────────────────────────

_BCRYPT_ROUNDS = 12
_MAX_PASSWORD_BYTES = 72  # bcrypt limit


def hash_password(password: str) -> str:
    """Hash a password with bcrypt (truncated to bcrypt's 72-byte limit)."""
    raw = password.encode("utf-8")[:_MAX_PASSWORD_BYTES]
    return bcrypt.hashpw(raw, bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)).decode("ascii")


def verify_password(password: str, hashed: str) -> bool:
    """Constant-time password verification."""
    try:
        return bcrypt.checkpw(password.encode("utf-8")[:_MAX_PASSWORD_BYTES],
                              hashed.encode("ascii"))
    except (ValueError, TypeError):
        return False


# ── Session tokens ───────────────────────────────────────────────────────────

_TOKEN_TTL_SECONDS = 30 * 24 * 3600  # 30 days


def new_session_token() -> str:
    """Generate an opaque bearer token (random, high entropy)."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """One-way hash of a session token for DB storage."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def session_expiry_iso(now_iso: str) -> str:
    """Return expiry ISO string 30 days after now_iso."""
    from datetime import datetime, timedelta, timezone
    now = datetime.fromisoformat(now_iso)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return (now + timedelta(seconds=_TOKEN_TTL_SECONDS)).isoformat()


# ── External URL validation (SSRF / safe redirect) ───────────────────────────

_SCHEME_OK = {"http", "https"}
_PRIVATE_HOST_PATTERNS = (
    re.compile(r"^localhost$", re.I),
    re.compile(r"^127\.", re.I),
    re.compile(r"^0\.0\.0\.0$", re.I),
    re.compile(r"^10\.", re.I),
    re.compile(r"^192\.168\.", re.I),
    re.compile(r"^169\.254\.", re.I),
    re.compile(r"^172\.(1[6-9]|2\d|3[01])\.", re.I),
    re.compile(r"^::1$", re.I),
    re.compile(r"\.local$", re.I),
    re.compile(r"\.internal$", re.I),
)


def validate_external_url(url: Optional[str]) -> tuple[bool, Optional[str]]:
    """
    Validate a job's source URL for safe external redirect.

    Returns (is_valid, normalized_url). Only http/https with a public-looking
    hostname pass. Anything else (file:, javascript:, localhost, private IP
    ranges, link-local metadata addresses) is blocked.
    """
    if not url:
        return False, None
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return False, None
    if parsed.scheme.lower() not in _SCHEME_OK:
        return False, None
    host = parsed.hostname
    if not host:
        return False, None
    for pattern in _PRIVATE_HOST_PATTERNS:
        if pattern.search(host):
            return False, None
    # Literal IP addresses must be public.
    try:
        ip = ipaddress.ip_address(host)
        if not ip.is_global:
            return False, None
    except ValueError:
        pass  # not an IP literal — hostname is fine
    # Rebuild a clean URL (drop credentials, keep path/query/fragment).
    clean = f"{parsed.scheme.lower()}://{host}"
    if parsed.port and parsed.port not in (80, 443):
        clean += f":{parsed.port}"
    clean += parsed.path or ""
    if parsed.query:
        clean += f"?{parsed.query}"
    if parsed.fragment:
        clean += f"#{parsed.fragment}"
    return True, clean


def apply_host(url: Optional[str]) -> Optional[str]:
    """Hostname of a URL for display next to the Apply button."""
    if not url:
        return None
    try:
        return urlparse(url).hostname
    except ValueError:
        return None


# ── Client identity (rate limiting / audit) ──────────────────────────────────

# Conservative shape check: IPv4, IPv6 or bracketed IPv6. Anything else is
# discarded so a spoofed header can never inject content into logs or create
# unbounded rate-limit keys.
_SAFE_CLIENT_TOKEN = re.compile(r"^\[?[0-9a-fA-F:.]{3,45}\]?$")


def client_ip(request: Any) -> str:
    """
    Best-effort client IP used for rate limiting and request audit.

    Proxy headers (X-Forwarded-For / X-Real-IP) are honoured **only** when
    DEDAN_TRUST_PROXY is enabled. Trusting them unconditionally would let any
    client invent an origin and escape per-IP rate limits, so the default is
    the direct peer address.
    """
    direct = getattr(getattr(request, "client", None), "host", None) or "unknown"

    try:
        from config.settings import get_settings
        trust_proxy = bool(get_settings().DEDAN_TRUST_PROXY)
    except Exception:
        trust_proxy = False
    if not trust_proxy:
        return direct

    headers = getattr(request, "headers", None)
    if headers is None:
        return direct

    forwarded = headers.get("x-forwarded-for")
    if forwarded:
        # Left-most entry is the original client; the rest are proxies.
        candidate = forwarded.split(",")[0].strip()
        if candidate and _SAFE_CLIENT_TOKEN.match(candidate):
            return candidate

    real = headers.get("x-real-ip")
    if real:
        candidate = real.strip()
        if candidate and _SAFE_CLIENT_TOKEN.match(candidate):
            return candidate

    return direct


# ── Simple in-memory sliding-window rate limiter ─────────────────────────────

class RateLimiter:
    """Sliding-window rate limiter (per-process, no external dependencies)."""

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str, limit: int, window_seconds: int) -> bool:
        """Record a hit for key; False when the limit is exceeded."""
        now = time.monotonic()
        window = self._hits[key]
        cutoff = now - window_seconds
        while window and window[0] <= cutoff:
            window.popleft()
        if len(window) >= limit:
            return False
        window.append(now)
        return True

    def reset(self) -> None:
        self._hits.clear()


# Shared limiter instance used by the app.
rate_limiter = RateLimiter()
