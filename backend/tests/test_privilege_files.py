from __future__ import annotations

import shutil
import stat
import subprocess
import sys
import re
from pathlib import Path

import pytest


def test_database_permission_paths() -> None:
    from backend.database import database_permission_paths

    database, wal, shm = database_permission_paths(Path("/var/lib/pideck/pideck.db"))
    assert database == Path("/var/lib/pideck/pideck.db")
    assert wal == Path("/var/lib/pideck/pideck.db-wal")
    assert shm == Path("/var/lib/pideck/pideck.db-shm")


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes are not enforced on Windows")
def test_restrict_sets_owner_only_mode_on_wal_and_shm(tmp_path: Path) -> None:
    from backend.database import _restrict_db_file

    paths = [
        tmp_path / "pideck.db",
        tmp_path / "pideck.db-wal",
        tmp_path / "pideck.db-shm",
    ]
    for path in paths:
        path.write_bytes(b"x")
        path.chmod(0o644)
    _restrict_db_file(paths[0])
    for path in paths:
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_relocate_moves_secret_without_touching_sqlite(tmp_path: Path) -> None:
    from backend.privilege_files import RelocateResult, apply_secret_relocation

    secret = "s" * 48
    env = tmp_path / ".env"
    env.write_text(f"PIDECK_HOST=127.0.0.1\nPIDECK_SESSION_SECRET={secret}\nPIDECK_PORT=8080\n", encoding="utf-8")
    database = tmp_path / "pideck.db"
    database.write_bytes(b"sqlite-bytes")
    credential = tmp_path / "nested" / "session.secret"
    status = apply_secret_relocation(env, credential)
    assert status == "moved"
    assert database.read_bytes() == b"sqlite-bytes"
    assert secret not in env.read_text(encoding="utf-8")
    assert "PIDECK_HOST=127.0.0.1" in env.read_text(encoding="utf-8")
    assert "PIDECK_PORT=8080" in env.read_text(encoding="utf-8")
    assert credential.read_text(encoding="utf-8").strip() == secret
    assert secret not in repr(RelocateResult(env.read_text(encoding="utf-8"), secret, "moved"))
    original = credential.read_bytes()
    assert apply_secret_relocation(env, credential) == "kept-existing"
    assert credential.read_bytes() == original


def test_relocate_does_not_overwrite_a_weak_credential(tmp_path: Path) -> None:
    from backend.privilege_files import apply_secret_relocation

    env = tmp_path / ".env"
    env.write_text("PIDECK_SESSION_SECRET=" + ("s" * 48) + "\n", encoding="utf-8")
    credential = tmp_path / "session.secret"
    credential.write_text("short\n", encoding="utf-8")
    assert apply_secret_relocation(env, credential) == "weak-existing"
    assert credential.read_text(encoding="utf-8") == "short\n"
    assert "PIDECK_SESSION_SECRET" in env.read_text(encoding="utf-8")


def _commands(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.strip().startswith("#"))


def test_deploy_scripts_reject_root_and_do_not_widen_sudo() -> None:
    root = Path(__file__).resolve().parents[2]
    install = (root / "scripts" / "install.sh").read_text(encoding="utf-8")
    update = (root / "scripts" / "update.sh").read_text(encoding="utf-8")
    migrate = (root / "scripts" / "migrate-privileges.sh").read_text(encoding="utf-8")
    rollback = (root / "scripts" / "rollback-privileges.sh").read_text(encoding="utf-8")
    for text in (install, update, migrate, rollback):
        assert "chown -R" not in text
        assert "NOPASSWD" not in text
        assert "git reset" not in text
        assert "git clean" not in text
    assert "$(id -un)" not in update
    assert '--user "$(id -un)"' not in install
    assert '--group "$(id -gn)"' not in install
    accounts = (root / "scripts" / "ensure-accounts.sh").read_text(encoding="utf-8")
    assert "--user pideck" in accounts
    assert "--group pideck" in accounts
    assert "--user pideck-term" in accounts
    assert "install_pideck_units" in install
    assert "install_pideck_units" in update
    assert '"$(id -u)" -eq 0' in update
    assert '"$(id -u)" -eq 0' in migrate
    for name in ("tailscale", "hostapd", "dnsmasq", "nft", "pihole", "NetworkManager"):
        assert name not in _commands(migrate)
        assert name not in _commands(rollback)
    assert "env.bak" in migrate
    assert "pideck.service.bak" in migrate
    assert "session.secret" in migrate
    assert "--confirm-restore-previous-unit" in rollback
    assert re.search(r"(^|\s)rm\s", _commands(migrate)) is None
    assert re.search(r"(^|\s)rm\s", _commands(rollback)) is None


def test_shell_scripts_have_unix_newlines() -> None:
    root = Path(__file__).resolve().parents[2] / "scripts"
    for path in root.glob("*.sh"):
        assert b"\r" not in path.read_bytes(), path.name


def test_shell_scripts_parse() -> None:
    candidates = [
        Path(r"C:\Program Files\Git\bin\bash.exe"),
        Path(shutil.which("bash") or ""),
    ]
    bash = next((path for path in candidates if path.is_file()), None)
    if bash is None:
        pytest.skip("bash is not installed")
    probe = subprocess.run([str(bash), "-n", "-c", "true"], capture_output=True)
    if probe.returncode == 127:
        pytest.skip("bash on PATH cannot run scripts")
    root = Path(__file__).resolve().parents[2] / "scripts"
    for path in sorted(root.glob("*.sh")):
        subprocess.run([str(bash), "-n", str(path)], check=True)
