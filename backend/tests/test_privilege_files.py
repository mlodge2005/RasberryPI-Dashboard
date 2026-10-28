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
    assert "grant_parent_execute" in install
    assert "grant_parent_execute" in update
    assert install.index("grant_parent_execute") < install.index("apply-secret")
    assert update.index("grant_parent_execute") < update.index("install_pideck_units")
    assert "u:pideck:--x,u:pideck-term:--x,u:pideck-wifi:--x,u:pideck-discover:--x,u:pideck-deauth:--x" in accounts
    assert "setfacl -R" not in accounts
    assert "setfacl -d" not in accounts
    assert "command -v setfacl" in accounts
    assert "Refusing to change home-directory mode bits." in accounts
    assert "sudo test -f" in accounts
    assert "${database}-wal" in accounts
    assert "${database}-shm" in accounts
    migrate_commands = _commands(migrate)
    assert migrate_commands.index("grant_parent_execute") < migrate_commands.index("apply-secret")
    assert migrate_commands.index("systemctl stop pideck.service") < migrate_commands.index("own_runtime_files")
    assert "setfacl -R" not in migrate
    assert "chmod o+x" not in migrate_commands
    for line in migrate_commands.splitlines():
        if "systemctl" in line and "stop" in line:
            assert line.strip() == "sudo systemctl stop pideck.service"
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


_HARNESS = r"""
set -euo pipefail
source "$1"
log="$2"
mode="$3"
: > "$log"
sudo() {
  printf 'sudo %s\n' "$*" >> "$log"
  local cmd="$1"
  shift
  case "$cmd" in
    getfacl)
      printf '%s\n' "$GETFACL_BODY"
      ;;
    test|grep|mkdir)
      command "$cmd" "$@"
      ;;
  esac
}
setfacl() { return 0; }
getfacl() { return 0; }
stat() {
  local path="${!#}"
  case "$path" in
    /home/admin) printf '%s\n' "$MODE_ADMIN" ;;
    /home) printf '%s\n' "$MODE_HOME" ;;
    *) printf '%s\n' 755 ;;
  esac
}
fail() { printf '%s\n' "$1" >&2; exit 1; }
dirname() {
  local path="${1%/}"
  if [[ "$path" == /*/* ]]; then
    printf '%s\n' "${path%/*}"
  else
    printf '/\n'
  fi
}

case "$mode" in
  acl-private-parent)
    MODE_ADMIN=750
    MODE_HOME=755
    GETFACL_BODY=$'user:pideck:--x\nuser:pideck-term:--x\nuser:pideck-wifi:--x\nuser:pideck-discover:--x\nuser:pideck-deauth:--x'
    grant_parent_execute /home/admin/RasberryPI-Dashboard
    grant_parent_execute /home/admin/RasberryPI-Dashboard
    count="$(grep -c 'setfacl -m u:pideck:--x,u:pideck-term:--x,u:pideck-wifi:--x,u:pideck-discover:--x,u:pideck-deauth:--x -- /home/admin$' "$log" || true)"
    [[ "$count" -eq 2 ]] || fail "expected two identical ACLs on /home/admin, got $count"
    grep -q '/home$' "$log" && fail "world-executable /home was changed"
    grep -q 'setfacl .* -- /$' "$log" && fail "filesystem root was changed"
    grep -q -- '-R' "$log" && fail "ACL was recursive"
    grep -q 'chmod' "$log" && fail "mode bits were changed"
    ;;
  acl-both-private)
    MODE_ADMIN=700
    MODE_HOME=700
    GETFACL_BODY=$'user:pideck:--x\nuser:pideck-term:--x\nuser:pideck-wifi:--x\nuser:pideck-discover:--x\nuser:pideck-deauth:--x'
    grant_parent_execute /home/admin/RasberryPI-Dashboard
    grep -q 'setfacl -m u:pideck:--x,u:pideck-term:--x,u:pideck-wifi:--x,u:pideck-discover:--x,u:pideck-deauth:--x -- /home/admin$' "$log" || fail "admin home was skipped"
    grep -q 'setfacl -m u:pideck:--x,u:pideck-term:--x,u:pideck-wifi:--x,u:pideck-discover:--x,u:pideck-deauth:--x -- /home$' "$log" || fail "private /home was skipped"
    grep -q 'setfacl .* -- /$' "$log" && fail "filesystem root was changed"
    ;;
  acl-gap)
    MODE_ADMIN=751
    MODE_HOME=700
    GETFACL_BODY=$'user:pideck:--x\nuser:pideck-term:--x\nuser:pideck-wifi:--x\nuser:pideck-discover:--x\nuser:pideck-deauth:--x'
    grant_parent_execute /home/admin/RasberryPI-Dashboard
    grep -q '/home/admin$' "$log" && fail "already-traversable parent received an ACL"
    grep -q 'setfacl -m u:pideck:--x,u:pideck-term:--x,u:pideck-wifi:--x,u:pideck-discover:--x,u:pideck-deauth:--x -- /home$' "$log" || fail "private ancestor was skipped"
    ;;
  acl-missing)
    old_path="$PATH"
    unset -f setfacl getfacl
    export PATH="/nonexistent-acl-path"
    if grant_parent_execute /home/admin/RasberryPI-Dashboard; then
      fail "missing acl tools were accepted"
    fi
    export PATH="$old_path"
    grep -q 'chmod' "$log" && fail "missing acl tools changed mode bits"
    ;;
  acl-rejected)
    MODE_ADMIN=700
    MODE_HOME=755
    GETFACL_BODY=$'user:pideck:r-x\nuser:pideck-term:--x'
    if grant_parent_execute /home/admin/RasberryPI-Dashboard; then
      fail "a read ACL was accepted"
    fi
    count="$(grep -c 'setfacl ' "$log" || true)"
    [[ "$count" -eq 1 ]] || fail "ACL attempt continued after rejection, count $count"
    ;;
  own-database)
    repo=/tmp/pideck-own-$$
    rm -rf "$repo"
    mkdir -p "$repo/data"
    printf '%s\n' 'PIDECK_SESSION_SECRET=super-secret-marker' 'PIDECK_DATABASE="./data/pideck.db"' > "$repo/.env"
    : > "$repo/data/pideck.db"
    : > "$repo/data/pideck.db-wal"
    : > "$repo/data/pideck.db-shm"
    own_runtime_files "$repo"
    own_runtime_files "$repo"
    base="$(readlink -f -- "$repo/data/pideck.db")"
    for sibling in "$base" "${base}-wal" "${base}-shm"; do
      count="$(grep -c "sudo chown pideck:pideck ${sibling}$" "$log" || true)"
      [[ "$count" -eq 2 ]] || fail "expected two chowns for $sibling, got $count"
      count="$(grep -c "sudo chmod 600 ${sibling}$" "$log" || true)"
      [[ "$count" -eq 2 ]] || fail "expected two mode updates for $sibling, got $count"
    done
    grep -q 'super-secret-marker' "$log" && fail "session secret was logged"
    grep -q 'chown -R' "$log" && fail "ownership change was recursive"
    printf '%s\n' 'PIDECK_DATABASE=../escaped.db' > "$repo/.env"
    if own_runtime_files "$repo"; then
      fail "a parent-escaping database path was accepted"
    fi
    grep -q 'escaped.db' "$log" && fail "parent-escaping database path was reowned"
    outside=/tmp/pideck-outside-$$
    mkdir -p "$outside"
    : > "$outside/app.db"
    : > "$outside/app.db-wal"
    : > "$outside/app.db-shm"
    printf '%s\n' "PIDECK_DATABASE=$outside/app.db" > "$repo/.env"
    : > "$log"
    own_runtime_files "$repo"
    external="$(readlink -f -- "$outside/app.db")"
    parent="$(dirname "$external")"
    grep -q "sudo chown pideck:pideck ${external}$" "$log" || fail "external database was skipped"
    grep -q "sudo chown pideck:pideck ${external}-wal$" "$log" || fail "external wal was skipped"
    grep -q "sudo chown pideck:pideck ${external}-shm$" "$log" || fail "external shm was skipped"
    grep -qx "sudo chown pideck:pideck $parent" "$log" && fail "external parent directory was reowned"
    grep -qx "sudo chmod 750 $parent" "$log" && fail "external parent directory mode was changed"
    rm -rf "$repo" "$outside"
    ;;
  *)
    fail "unknown mode $mode"
    ;;
esac
exit 0
"""


def _bash_executable() -> Path | None:
    candidates = [
        Path(r"C:\Program Files\Git\bin\bash.exe"),
        Path(shutil.which("bash") or ""),
    ]
    bash = next((path for path in candidates if path.is_file()), None)
    if bash is None:
        return None
    probe = subprocess.run([str(bash), "-n", "-c", "true"], capture_output=True)
    if probe.returncode == 127:
        return None
    return bash


@pytest.mark.parametrize(
    "mode",
    [
        "acl-private-parent",
        "acl-both-private",
        "acl-gap",
        "acl-missing",
        "acl-rejected",
        "own-database",
    ],
)
def test_migration_acl_and_database_ownership(tmp_path: Path, mode: str) -> None:
    bash = _bash_executable()
    if bash is None:
        pytest.skip("bash is not installed")
    script = Path(__file__).resolve().parents[2] / "scripts" / "ensure-accounts.sh"
    harness = tmp_path / "harness.sh"
    log = tmp_path / "commands.log"
    harness.write_text(_HARNESS, encoding="utf-8", newline="\n")
    completed = subprocess.run(
        [str(bash), str(harness), str(script), str(log), mode],
        capture_output=True,
        text=True,
    )
    details = completed.stderr + completed.stdout
    if log.is_file():
        details += "\n--- log ---\n" + log.read_text(encoding="utf-8", errors="replace")
    assert completed.returncode == 0, details

