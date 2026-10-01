"""Shared authentication dependency. Routes should not reimplement cookie checks."""

from __future__ import annotations

from fastapi import HTTPException, Request

from backend.auth.sessions import COOKIE_NAME, user_from_token
from backend.database import UserRecord


def get_current_user(request: Request) -> UserRecord:
    cached = getattr(request.state, "user", None)
    if isinstance(cached, UserRecord):
        return cached
    token = request.cookies.get(COOKIE_NAME)
    user = user_from_token(token, request.app.state.session_secret)
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    request.state.user = user
    return user
