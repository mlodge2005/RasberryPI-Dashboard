"""Login, logout, and the current user."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from backend.auth.dependencies import get_current_user
from backend.auth.rate_limit import LoginRateLimiter
from backend.auth.sessions import (
    authenticate,
    clear_session_cookie,
    issue_session,
    profile_for,
    revoke_token,
    set_session_cookie,
    COOKIE_NAME,
)
from backend.config import Settings
from backend.database import UserRecord
from backend.models.schemas import LoginRequest, UserProfile
from backend.utils.security import client_key

logger = logging.getLogger(__name__)

router = APIRouter()
INCORRECT_LOGIN = "Incorrect username or password"


@router.post("/login", response_model=UserProfile)
def login(body: LoginRequest, request: Request, response: Response) -> UserProfile:
    settings: Settings = request.app.state.settings
    limiter: LoginRateLimiter = request.app.state.rate_limiter
    host = request.client.host if request.client else None
    key = client_key(request.headers, host)
    if limiter.too_many(key):
        raise HTTPException(status_code=429, detail="Too many login attempts. Try again later.")
    user = authenticate(body.username.strip(), body.password)
    if user is None:
        limiter.add_failure(key)
        safe_name = body.username.replace("\r", "").replace("\n", "")[:64]
        logger.info("Failed login for %s", safe_name)
        raise HTTPException(status_code=401, detail=INCORRECT_LOGIN)
    token = issue_session(user.id, request.app.state.session_secret, settings.pideck_session_hours)
    set_session_cookie(response, request, token, settings)
    logger.info("User %s logged in", user.username)
    return profile_for(user)


@router.post("/logout")
def logout(
    request: Request,
    response: Response,
    user: UserRecord = Depends(get_current_user),
) -> dict[str, str]:
    settings: Settings = request.app.state.settings
    revoke_token(request.cookies.get(COOKIE_NAME), request.app.state.session_secret)
    clear_session_cookie(response, request, settings)
    logger.info("User %s logged out", user.username)
    return {"detail": "Logged out"}


@router.get("/me", response_model=UserProfile)
def me(user: UserRecord = Depends(get_current_user)) -> UserProfile:
    return profile_for(user)
