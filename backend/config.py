"""Process configuration loaded from the environment and the repo .env file."""

from __future__ import annotations

import logging
import secrets
import sys
from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parent.parent
logger = logging.getLogger(__name__)

_LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_WEAK_SECRETS = frozenset(
    {
        "",
        "replace-with-a-long-random-string",
        "changeme",
        "change-me",
        "secret",
        "password",
    }
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    pideck_host: str = "127.0.0.1"
    pideck_port: int = 8080
    pideck_database: str = "./data/pideck.db"
    pideck_session_secret: str = ""
    pideck_session_hours: int = 24
    pideck_cookie_secure: str = "auto"
    pideck_dev: bool = False
    pideck_allow_non_localhost: bool = False

    @field_validator("pideck_host", "pideck_database", "pideck_session_secret", "pideck_cookie_secure", mode="before")
    @classmethod
    def strip_strings(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("pideck_port")
    @classmethod
    def port_range(cls, value: int) -> int:
        if value < 1 or value > 65535:
            raise ValueError("PIDECK_PORT must be between 1 and 65535")
        return value

    @field_validator("pideck_session_hours")
    @classmethod
    def session_hours(cls, value: int) -> int:
        if value < 1 or value > 168:
            raise ValueError("PIDECK_SESSION_HOURS must be between 1 and 168")
        return value

    @field_validator("pideck_cookie_secure")
    @classmethod
    def cookie_mode(cls, value: str) -> str:
        mode = value.strip().lower()
        allowed = {"auto", "true", "false", "1", "0", "yes", "no"}
        if mode not in allowed:
            raise ValueError("PIDECK_COOKIE_SECURE must be auto, true, or false")
        return mode

    @property
    def database_path(self) -> Path:
        path = Path(self.pideck_database)
        if not path.is_absolute():
            path = REPO_ROOT / path
        return path

    def validate_bind(self) -> None:
        host = self.pideck_host.strip().lower()
        if host in _LOCAL_HOSTS:
            return
        if self.pideck_allow_non_localhost:
            logger.warning(
                "PIDECK_ALLOW_NON_LOCALHOST is set. PiDeck will bind to %s. "
                "Do not expose this port to the public internet.",
                self.pideck_host,
            )
            return
        raise SystemExit(
            f"Refusing to bind to {self.pideck_host}. PiDeck listens on localhost only. "
            "Reach it through Tailscale Serve. Set PIDECK_ALLOW_NON_LOCALHOST=true only "
            "if you accept the risk of a non-local bind."
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()


def resolve_session_secret(settings: Settings) -> str:
    """Return the HMAC secret used to store session tokens.

    A placeholder from .env.example is accepted only while PIDECK_DEV is true,
    and then replaced with an ephemeral secret so development can start quickly.
    Production refuses to boot with a missing or well-known secret.
    """
    secret = settings.pideck_session_secret.strip()
    weak = secret.lower() in _WEAK_SECRETS or len(secret) < 32
    if not weak:
        return secret
    if settings.pideck_dev and secret.lower() in {"", "replace-with-a-long-random-string"}:
        logger.warning(
            "PIDECK_DEV is true and PIDECK_SESSION_SECRET is unset or still the example "
            "placeholder. Using an ephemeral secret for this process. Sessions reset on restart."
        )
        return secrets.token_urlsafe(48)
    raise SystemExit(
        "Set PIDECK_SESSION_SECRET to a random string of at least 32 characters.\n"
        'Generate one with: python -c "import secrets; print(secrets.token_urlsafe(48))"'
    )


def assert_process_not_binding_public() -> None:
    """Stop startup if the process arguments ask for a public bind address."""
    if get_settings().pideck_allow_non_localhost:
        return
    joined = " ".join(sys.argv).lower()
    if "0.0.0.0" in joined or "[::]" in joined or "--host *" in joined:
        raise SystemExit(
            "Refusing to start because the process arguments include a public bind address. "
            "Use python -m backend, which binds PIDECK_HOST (127.0.0.1 by default)."
        )
