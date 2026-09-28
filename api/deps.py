"""FastAPI dependencies: current user resolution, rate limiting, errors."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import Depends, Header, HTTPException, Request, status

from api.security import client_ip, hash_token, rate_limiter
from api.store import get_user_store


class ApiError(HTTPException):
    """Structured API error — never leaks internals to clients."""

    def __init__(
        self, status_code: int, code: str, message: str, headers: Optional[dict[str, str]] = None
    ) -> None:
        super().__init__(
            status_code=status_code,
            detail={"code": code, "message": message},
            headers=headers,
        )


def _extract_token(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return None


def get_current_user_optional(
    authorization: Optional[str] = Header(default=None),
) -> Optional[dict[str, Any]]:
    """Resolve the signed-in user or None (public endpoints)."""
    token = _extract_token(authorization)
    if not token:
        return None
    store = get_user_store()
    user_id = store.get_session_user(hash_token(token))
    if not user_id:
        return None
    user = store.get_user(user_id)
    if not user:
        return None
    user.pop("password_hash", None)
    return user


def get_current_user(
    user: Optional[dict[str, Any]] = Depends(get_current_user_optional),
) -> dict[str, Any]:
    """Require a signed-in user."""
    if user is None:
        raise ApiError(
            status.HTTP_401_UNAUTHORIZED,
            "auth_required",
            "Sign in to use this feature.",
        )
    return user


def enforce_rate_limit(request: Request, bucket: str, limit: int, window: int = 60) -> None:
    """
    Per-IP sliding-window limit; raises 429 when exceeded.

    The identity comes from api.security.client_ip, so limits keep working
    behind a reverse proxy (when configured) while staying unspoofable when
    the app is exposed directly. `Retry-After` tells well-behaved clients how
    long to wait instead of hammering the endpoint.
    """
    key = f"{bucket}:{client_ip(request)}"
    if not rate_limiter.allow(key, limit, window):
        raise ApiError(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "rate_limited",
            "Too many requests — please slow down.",
            headers={"Retry-After": str(window)},
        )


def require_system_token(
    request: Request,
    x_system_token: Optional[str] = Header(default=None),
) -> None:
    """Protect /api/system with a configured token (503 when unset)."""
    from config.settings import get_settings

    expected = get_settings().DEDAN_SYSTEM_TOKEN
    if not expected:
        raise ApiError(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "not_configured",
            "System dashboard is not configured on this deployment.",
        )
    import secrets

    if not x_system_token or not secrets.compare_digest(x_system_token, expected):
        raise ApiError(
            status.HTTP_401_UNAUTHORIZED,
            "invalid_token",
            "Invalid or missing system token.",
        )
