"""Server-side sessions stored as HMAC digests. The raw token only lives in the cookie."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets

from fastapi import Response
from starlette.requests import HTTPConnection

from backend.auth.passwords import hash_password, needs_rehash, verify_password
from backend.config import Settings
from backend.database import (
    UserRecord,
    create_session,
    delete_session,
    get_theme_json,
    get_user_by_id,
    get_user_by_token_hash,
    get_user_by_username,
    update_password_hash,
)
from backend.models.schemas import ThemeSettings, UserProfile, default_theme
from backend.utils.security import cookie_is_secure

logger = logging.getLogger(__name__)

COOKIE_NAME = "pideck_session"
_dummy_hash: str | None = None


def _dummy_password_hash() -> str:
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = hash_password(secrets.token_urlsafe(24))
    return _dummy_hash


def token_digest(token: str, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), token.encode("utf-8"), hashlib.sha256).hexdigest()


def authenticate(username: str, password: str) -> UserRecord | None:
    user = get_user_by_username(username)
    if user is None:
        verify_password(password, _dummy_password_hash())
        return None
    if not verify_password(password, user.password_hash):
        return None
    if needs_rehash(user.password_hash):
        update_password_hash(user.id, hash_password(password))
        refreshed = get_user_by_id(user.id)
        return refreshed or user
    return user


def issue_session(user_id: int, secret: str, hours: int) -> str:
    token = secrets.token_urlsafe(32)
    create_session(user_id, token_digest(token, secret), hours)
    return token


def user_from_token(token: str | None, secret: str) -> UserRecord | None:
    if not token or len(token) > 512:
        return None
    return get_user_by_token_hash(token_digest(token, secret))


def revoke_token(token: str | None, secret: str) -> None:
    if not token:
        return
    delete_session(token_digest(token, secret))


def load_theme(user_id: int) -> ThemeSettings:
    raw = get_theme_json(user_id)
    if not raw:
        return default_theme()
    try:
        return ThemeSettings.model_validate(json.loads(raw))
    except (ValueError, TypeError):
        logger.warning("Ignoring invalid theme settings for user %s", user_id)
        return default_theme()


def profile_for(user: UserRecord) -> UserProfile:
    return UserProfile(id=user.id, username=user.username, theme=load_theme(user.id))


def set_session_cookie(response: Response, connection: HTTPConnection, token: str, settings: Settings) -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        secure=cookie_is_secure(connection, settings),
        samesite="lax",
        max_age=settings.pideck_session_hours * 3600,
        path="/",
    )


def clear_session_cookie(response: Response, connection: HTTPConnection, settings: Settings) -> None:
    response.delete_cookie(
        key=COOKIE_NAME,
        path="/",
        httponly=True,
        samesite="lax",
        secure=cookie_is_secure(connection, settings),
    )
