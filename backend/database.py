"""SQLite storage for users, sessions, and per-user settings.

Schema changes are additive (`CREATE TABLE IF NOT EXISTS` only). Nothing here
drops or rewrites existing rows.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

from backend.config import get_settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);

CREATE TABLE IF NOT EXISTS user_settings (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    theme_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS battery_config (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    estimated_full_runtime_minutes REAL,
    full_charge_time_minutes REAL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS battery_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    last_seen_at TEXT NOT NULL,
    duration_seconds INTEGER,
    estimated_start_percent REAL,
    estimated_end_percent REAL,
    power_source TEXT NOT NULL,
    uncertain INTEGER NOT NULL DEFAULT 0,
    start_reason TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_battery_sessions_open ON battery_sessions(ended_at);

CREATE TABLE IF NOT EXISTS battery_calibration_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recorded_at TEXT NOT NULL,
    duration_seconds INTEGER NOT NULL,
    session_id INTEGER
);
"""


@dataclass(frozen=True)
class UserRecord:
    id: int
    username: str
    password_hash: str

    def __repr__(self) -> str:
        return f"UserRecord(id={self.id}, username={self.username!r})"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    path = get_settings().database_path
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=5.0)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_db() -> None:
    with connect() as connection:
        connection.executescript(SCHEMA)
        connection.execute("DELETE FROM sessions WHERE expires_at <= ?", (_iso(utc_now()),))
    _restrict_db_file(get_settings().database_path)


def _restrict_db_file(path: Path) -> None:
    if sys.platform == "win32" or not path.exists():
        return
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def count_users() -> int:
    with connect() as connection:
        row = connection.execute("SELECT COUNT(*) AS count FROM users").fetchone()
    return int(row["count"]) if row else 0


def user_exists(username: str) -> bool:
    return get_user_by_username(username) is not None


def create_user(username: str, password_hash: str) -> UserRecord:
    created_at = _iso(utc_now())
    with connect() as connection:
        cursor = connection.execute(
            "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
            (username, password_hash, created_at),
        )
        user_id = int(cursor.lastrowid)
    created = get_user_by_id(user_id)
    if created is None:
        raise RuntimeError("User insert did not persist")
    return created


def get_user_by_username(username: str) -> UserRecord | None:
    with connect() as connection:
        row = connection.execute(
            "SELECT id, username, password_hash FROM users WHERE username = ? COLLATE NOCASE",
            (username,),
        ).fetchone()
    return _user_from_row(row)


def get_user_by_id(user_id: int) -> UserRecord | None:
    with connect() as connection:
        row = connection.execute(
            "SELECT id, username, password_hash FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
    return _user_from_row(row)


def update_password_hash(user_id: int, password_hash: str) -> None:
    with connect() as connection:
        connection.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (password_hash, user_id),
        )


def _user_from_row(row: sqlite3.Row | None) -> UserRecord | None:
    if row is None:
        return None
    return UserRecord(
        id=int(row["id"]),
        username=str(row["username"]),
        password_hash=str(row["password_hash"]),
    )


def create_session(user_id: int, token_hash: str, hours: int) -> None:
    now = utc_now()
    with connect() as connection:
        connection.execute("DELETE FROM sessions WHERE expires_at <= ?", (_iso(now),))
        connection.execute(
            """
            INSERT INTO sessions (user_id, token_hash, created_at, expires_at)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, token_hash, _iso(now), _iso(now + timedelta(hours=hours))),
        )


def get_user_by_token_hash(token_hash: str) -> UserRecord | None:
    now = _iso(utc_now())
    with connect() as connection:
        row = connection.execute(
            """
            SELECT users.id, users.username, users.password_hash, sessions.expires_at
            FROM sessions
            JOIN users ON users.id = sessions.user_id
            WHERE sessions.token_hash = ?
            """,
            (token_hash,),
        ).fetchone()
        if row is None:
            return None
        if str(row["expires_at"]) <= now:
            connection.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
            return None
        return UserRecord(
            id=int(row["id"]),
            username=str(row["username"]),
            password_hash=str(row["password_hash"]),
        )


def delete_session(token_hash: str) -> None:
    with connect() as connection:
        connection.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))


def get_theme_json(user_id: int) -> str | None:
    with connect() as connection:
        row = connection.execute(
            "SELECT theme_json FROM user_settings WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    if row is None:
        return None
    return str(row["theme_json"])


def save_theme_json(user_id: int, theme_json: str) -> None:
    now = _iso(utc_now())
    with connect() as connection:
        connection.execute(
            """
            INSERT INTO user_settings (user_id, theme_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                theme_json = excluded.theme_json,
                updated_at = excluded.updated_at
            """,
            (user_id, theme_json, now),
        )
