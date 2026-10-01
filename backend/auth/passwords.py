"""Argon2id password hashing and account-name rules."""

from __future__ import annotations

import re

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

# 32 MiB, parallelism 1: comfortable on a 1 GB Raspberry Pi 3 while staying
# above the OWASP Argon2id memory floor.
_hasher = PasswordHasher(time_cost=2, memory_cost=32768, parallelism=1)
_USERNAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{2,31}")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def validate_username(username: str) -> str | None:
    if _USERNAME.fullmatch(username) is None:
        return "Username must be 3-32 characters and use letters, numbers, _, ., or -."
    return None


def validate_new_password(password: str) -> str | None:
    if len(password) < 10:
        return "Password must be at least 10 characters."
    if len(password) > 256:
        return "Password must be at most 256 characters."
    return None
