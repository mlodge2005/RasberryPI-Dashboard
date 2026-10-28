"""Move the session secret into a root-owned credential file.

The functions return status words only. They do not log or return the secret
except inside `write_credential`, which the caller writes to disk and does
not print. SQLite files are not opened here.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from backend.config import secret_is_weak

_SECRET_KEY = "PIDECK_SESSION_SECRET"


@dataclass
class RelocateResult:
    env_text: str
    write_credential: str | None
    status: str

    def __repr__(self) -> str:
        writing = "yes" if self.write_credential else "no"
        return f"RelocateResult(status={self.status!r}, write={writing})"


def split_env_assignment(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None
    key, value = stripped.split("=", 1)
    key = key.strip()
    if key.startswith("export "):
        key = key[len("export ") :].strip()
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        value = value[1:-1]
    if not key:
        return None
    return key, value


def _secret_from_env(text: str) -> str | None:
    found: str | None = None
    for line in text.splitlines():
        parsed = split_env_assignment(line)
        if parsed is not None and parsed[0] == _SECRET_KEY:
            found = parsed[1]
    return found


def _without_secret_lines(text: str) -> str:
    kept: list[str] = []
    for line in text.splitlines(keepends=True):
        parsed = split_env_assignment(line)
        if parsed is not None and parsed[0] == _SECRET_KEY:
            continue
        kept.append(line)
    return "".join(kept)


def relocate_session_secret(env_text: str, existing_credential: str | None) -> RelocateResult:
    """Decide whether to write a credential and whether to strip .env.

    An existing non-empty credential is kept byte-for-byte. A weak value is
    left untouched so this never replaces a secret on its own.
    """
    current = None if existing_credential is None else existing_credential.strip()
    if current:
        if secret_is_weak(current):
            return RelocateResult(env_text, None, "weak-existing")
        return RelocateResult(_without_secret_lines(env_text), None, "kept-existing")
    found = _secret_from_env(env_text)
    if found is None or found.strip() == "":
        return RelocateResult(env_text, None, "missing")
    if secret_is_weak(found):
        return RelocateResult(env_text, None, "weak")
    return RelocateResult(_without_secret_lines(env_text), found.strip(), "moved")


def apply_secret_relocation(env_path: Path, credential_path: Path) -> str:
    """Write the credential only when it is missing. Return a status word."""
    original = env_path.read_text(encoding="utf-8")
    existing = credential_path.read_text(encoding="utf-8") if credential_path.is_file() else None
    result = relocate_session_secret(original, existing)
    if result.write_credential is not None:
        credential_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = credential_path.with_name(credential_path.name + ".tmp")
        if temporary.exists():
            temporary.unlink()
        fd = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, (result.write_credential + "\n").encode("utf-8"))
        finally:
            os.close(fd)
        if sys.platform != "win32":
            os.chmod(temporary, 0o600)
        os.replace(temporary, credential_path)
        if sys.platform != "win32":
            os.chmod(credential_path, 0o600)
    if result.env_text != original:
        temporary_env = env_path.with_name(env_path.name + ".tmp")
        temporary_env.write_text(result.env_text, encoding="utf-8", newline="\n")
        if sys.platform != "win32":
            os.chmod(temporary_env, 0o600)
        os.replace(temporary_env, env_path)
        if sys.platform != "win32":
            os.chmod(env_path, 0o600)
    return result.status
