"""Authentication: register, login, logout, current user."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Request, status

from api.deps import (
    ApiError,
    enforce_rate_limit,
    get_current_user,
    get_current_user_optional,
)
from api.schemas import AuthResponse, LoginRequest, RegisterRequest, UserOut
from api.security import (
    hash_password,
    hash_token,
    new_session_token,
    session_expiry_iso,
    verify_password,
)
from api.store import _now, get_user_store

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _user_out(user: dict) -> UserOut:
    return UserOut(
        id=user["id"], email=user["email"],
        display_name=user.get("display_name"),
        created_at=user["created_at"],
    )


@router.post("/register", response_model=AuthResponse,
             status_code=status.HTTP_201_CREATED)
def register(request: Request, payload: RegisterRequest) -> AuthResponse:
    """Create an account and return a bearer token."""
    enforce_rate_limit(request, "auth", limit=10, window=60)
    store = get_user_store()
    if store.get_user_by_email(payload.email):
        raise ApiError(409, "email_taken",
                       "An account with this email already exists.")
    user = store.create_user(
        payload.email, hash_password(payload.password), payload.display_name,
    )
    token = new_session_token()
    store.create_session(hash_token(token), user["id"],
                         session_expiry_iso(_now()))
    return AuthResponse(token=token, user=_user_out(user))


@router.post("/login", response_model=AuthResponse)
def login(request: Request, payload: LoginRequest) -> AuthResponse:
    """Sign in and receive a bearer token."""
    enforce_rate_limit(request, "auth", limit=10, window=60)
    store = get_user_store()
    user = store.get_user_by_email(payload.email)
    if not user or not verify_password(payload.password,
                                       user["password_hash"]):
        raise ApiError(401, "invalid_credentials",
                       "Incorrect email or password.")
    token = new_session_token()
    store.create_session(hash_token(token), user["id"],
                         session_expiry_iso(_now()))
    user.pop("password_hash", None)
    return AuthResponse(token=token, user=_user_out(user))


@router.post("/logout")
def logout(request: Request,
           user: dict = Depends(get_current_user)) -> dict:
    """Revoke the current session token."""
    enforce_rate_limit(request, "auth", limit=30, window=60)
    from fastapi import Header
    # Token is present because get_current_user succeeded via header.
    auth_header = request.headers.get("authorization", "")
    token = auth_header.split(None, 1)[1].strip() if len(
        auth_header.split(None, 1)) == 2 else ""
    if token:
        get_user_store().delete_session(hash_token(token))
    return {"logged_out": True}


@router.get("/me")
def me(user: dict = Depends(get_current_user)) -> dict:
    """Current user profile."""
    return {"user": _user_out(user).model_dump()}
