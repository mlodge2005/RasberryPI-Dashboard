from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

_HARNESS = r"""
set -euo pipefail
source "$1"
repo="$2"
PIDECK_FAKE_ROOT="$3"
PIDECK_COMMAND_LOG="$4"
mode="$5"
python="$6"
: > "$PIDECK_COMMAND_LOG"
declare -A META=()

remap_path() {
  case "$1" in
    /etc/*|/usr/local/*) printf '%s%s\n' "$PIDECK_FAKE_ROOT" "$1" ;;
    *) printf '%s\n' "$1" ;;
  esac
}

sudo() {
  printf 'sudo %s\n' "$*" >> "$PIDECK_COMMAND_LOG"
  local cmd="$1"
  shift
  case "$cmd" in
    visudo)
      command visudo "$@"
      ;;
    systemctl|systemd-tmpfiles)
      return 0
      ;;
    cat)
      command cat -- "$(remap_path "${!#}")"
      ;;
    stat)
      local mapped
      mapped="$(remap_path "${!#}")"
      [[ -f "$mapped" ]] || return 1
      [[ -n "${META[$mapped]+x}" ]] || return 1
      printf '%s\n' "${META[$mapped]}"
      ;;
    install)
      local mode_bits="755" src="" target="" make_dir=0
      while [[ $# -gt 0 ]]; do
        case "$1" in
          -d) make_dir=1; shift ;;
          -o|-g) shift 2 ;;
          -m) mode_bits="$2"; shift 2 ;;
          --) shift ;;
          *)
            if [[ "$make_dir" -eq 1 || -n "$src" ]]; then
              target="$1"
            else
              src="$1"
            fi
            shift
            ;;
        esac
      done
      target="$(remap_path "$target")"
      mkdir -p "$(dirname "$target")"
      if [[ "$make_dir" -eq 1 ]]; then
        mkdir -p "$target"
        return 0
      fi
      cp -- "$src" "$target"
      META["$target"]="root:root:${mode_bits}"
      ;;
    *)
      return 0
      ;;
  esac
}

systemd-analyze() {
  if [[ "${VERIFY_MODE:-}" == "fail" ]]; then
    echo "Unit pideck-discover.service has a bad unit file setting." >&2
    return 1
  fi
  local arg
  for arg in "$@"; do
    case "$arg" in
      *.service|*.socket|*.target|*.mount|*.timer|*.path)
        if [[ -f "$arg" ]]; then
          printf 'verify-file %s %s\n' "$(stat -c %a -- "$arg")" "$arg" >> "$PIDECK_COMMAND_LOG"
        fi
        ;;
    esac
  done
  command systemd-analyze "$@"
}

fail() { printf '%s\n' "$1" >&2; exit 1; }

installed="$PIDECK_FAKE_ROOT/etc/systemd/system/pideck-discover.service"

case "$mode" in
  replace-old-unit)
    install_pideck_units "$repo" "$python"
    grep -qx 'PrivateTmp=no' "$installed" || fail "discovery PrivateTmp was not updated"
    grep -qx 'ProtectSystem=strict' "$installed" || fail "ProtectSystem=strict was dropped"
    grep -qx 'ReadWritePaths=/tmp' "$installed" || fail "discovery cannot write /tmp"
    grep -q '__APP_DIR__\|__USER__\|__GROUP__' "$installed" && fail "placeholder reached the installed unit"
    grep -qx 'PrivateTmp=yes' "$installed" && fail "old PrivateTmp=yes remained"
    grep -q '^ExecStart=/.*backend/.venv/bin/python -m backend.discover_helper$' "$installed" || fail "discovery ExecStart was not rendered"
    deauth="$PIDECK_FAKE_ROOT/etc/systemd/system/pideck-deauth.service"
    grep -qx 'PrivateTmp=no' "$deauth" || fail "disconnect PrivateTmp was not updated"
    grep -qx 'ReadWritePaths=/tmp' "$deauth" || fail "disconnect cannot write /tmp"
    grep -qx 'User=pideck-deauth' "$deauth" || fail "disconnect account changed"
    grep -q '__APP_DIR__' "$deauth" && fail "disconnect placeholder remained"
    wifi="$PIDECK_FAKE_ROOT/etc/systemd/system/pideck-wifi.service"
    grep -qx 'User=pideck-wifi' "$wifi" || fail "wifi account changed"
    grep -qx 'NoNewPrivileges=yes' "$wifi" || fail "wifi privilege lock changed"
    grep -q '__APP_DIR__' "$wifi" && fail "wifi placeholder remained"
    terminal="$PIDECK_FAKE_ROOT/etc/systemd/system/pideck-terminal.service"
    grep -qx 'User=pideck-term' "$terminal" || fail "terminal account changed"
    dashboard="$PIDECK_FAKE_ROOT/etc/systemd/system/pideck.service"
    grep -qx 'User=pideck' "$dashboard" || fail "dashboard account changed"
    grep -q '__APP_DIR__\|__USER__\|__GROUP__' "$dashboard" && fail "dashboard placeholder remained"
    for unit in \
      pideck.service \
      pideck-terminal.service pideck-terminal.socket \
      pideck-wifi.service pideck-wifi.socket \
      pideck-discover.service pideck-discover.socket \
      pideck-deauth.service pideck-deauth.socket
    do
      [[ -s "$PIDECK_FAKE_ROOT/etc/systemd/system/$unit" ]] || fail "missing installed unit $unit"
      grep -q '__APP_DIR__\|__USER__\|__GROUP__' "$PIDECK_FAKE_ROOT/etc/systemd/system/$unit" && fail "placeholder in $unit"
    done
    grep -q 'ListenStream=/run/pideck/discover.sock' "$PIDECK_FAKE_ROOT/etc/systemd/system/pideck-discover.socket" || fail "discover socket changed"
    grep -q 'ListenStream=/run/pideck/deauth.sock' "$PIDECK_FAKE_ROOT/etc/systemd/system/pideck-deauth.socket" || fail "disconnect socket changed"
    grep -q 'install -o root -g root -m 644 -- '"$repo"'/systemd/pideck-discover.service /etc/systemd/system/pideck-discover.service' "$PIDECK_COMMAND_LOG" \
      || grep -q 'install -o root -g root -m 644 -- .*/pideck-discover.service /etc/systemd/system/pideck-discover.service' "$PIDECK_COMMAND_LOG" \
      || fail "discovery unit was not installed as root:root mode 644"
    grep -q 'install -o root -g root -m 440 -- '"$repo"'/sudoers/pideck-deauth /etc/sudoers.d/pideck-deauth' "$PIDECK_COMMAND_LOG" || fail "disconnect sudoers mode changed"
    install_at="$(grep -n '/etc/systemd/system/pideck-discover.service$' "$PIDECK_COMMAND_LOG" | tail -n 1 | cut -d: -f1)"
    reload_at="$(grep -n 'systemctl daemon-reload$' "$PIDECK_COMMAND_LOG" | tail -n 1 | cut -d: -f1)"
    [[ -n "$install_at" && -n "$reload_at" && "$install_at" -lt "$reload_at" ]] || fail "daemon-reload did not follow unit replacement"
    grep -q 'systemctl enable .*pideck-discover.socket .*pideck-deauth.socket .*pideck-deauth.service' "$PIDECK_COMMAND_LOG" || fail "required units were not enabled"
    grep -q 'systemctl start pideck-terminal.socket pideck-wifi.socket pideck-discover.socket pideck-deauth.socket' "$PIDECK_COMMAND_LOG" || fail "required sockets were not started"
    grep -E -q 'systemctl (restart|start|enable).*(hostapd|dnsmasq|NetworkManager|tailscaled)|nft' "$PIDECK_COMMAND_LOG" && fail "a network service command was recorded"
    [[ -s "$PIDECK_FAKE_ROOT/usr/local/lib/pideck/disconnect-client" ]] || fail "disconnect program was not installed"
    grep -q 'deauthenticate' "$PIDECK_FAKE_ROOT/usr/local/lib/pideck/disconnect-client" || fail "disconnect program lost its operation"
    ;;
  reject-invalid)
    VERIFY_MODE=fail
    if install_pideck_units "$repo" "$python"; then
      fail "invalid units were installed"
    fi
    grep -qx 'PrivateTmp=yes' "$installed" || fail "the previously installed unit was replaced"
    grep -q '__APP_DIR__' "$installed" || fail "the outdated unit was edited"
    grep -q 'daemon-reload' "$PIDECK_COMMAND_LOG" && fail "systemd was reloaded after a rejected unit"
    grep -q '/etc/systemd/system/pideck-discover.service' "$PIDECK_COMMAND_LOG" && fail "the discovery unit was copied after rejection"
    ;;
  verify-rejects)
    tmp="$(mktemp -d)"
    printf '%s\n' '[Service]' 'Type=simple' 'ExecStart=/no/such/pideck-python' > "$tmp/missing.service"
    if verify_units_before_install "$tmp/missing.service"; then
      fail "a missing executable was accepted"
    fi
    printf '%s\n' '[Service]' 'Type=simple' 'ExecStart=/bin/true' 'NotARealDirective=1' > "$tmp/unknown.service"
    if verify_units_before_install "$tmp/unknown.service"; then
      fail "an unknown directive was accepted"
    fi
    printf '%s\n' '[Service]' 'Type=simple' 'ExecStart=__APP_DIR__/backend/.venv/bin/python -m backend' > "$tmp/placeholder.service"
    if verify_units_before_install "$tmp/placeholder.service"; then
      fail "a placeholder was accepted"
    fi
    rm -rf "$tmp"
    ;;
  proxy-keeps-existing)
    proxy="$PIDECK_FAKE_ROOT/etc/systemd/system/pideck-local.service"
    mkdir -p "$(dirname "$proxy")"
    printf '%s\n' 'OLD-PROXY-UNIT' > "$proxy"
    out="$PIDECK_FAKE_ROOT/proxy-verify.out"
    if [[ -x /usr/bin/caddy ]]; then
      install_checked_unit "$repo/systemd/pideck-local.service" /etc/systemd/system/pideck-local.service
      grep -q 'User=caddy' "$proxy" || fail "existing proxy unit was not refreshed"
      grep -qx 'OLD-PROXY-UNIT' "$proxy" && fail "old proxy unit remained"
      grep -E -q 'verify-file 644 .*/pideck-local\.service$' "$PIDECK_COMMAND_LOG" || fail "validator did not receive a readable pideck-local.service"
    else
      if install_checked_unit "$repo/systemd/pideck-local.service" /etc/systemd/system/pideck-local.service >"$out" 2>&1; then
        fail "the proxy unit was accepted without caddy"
      fi
      grep -qx 'OLD-PROXY-UNIT' "$proxy" || fail "existing proxy unit was replaced"
      grep -q 'pideck-local.service' "$out" || fail "validation error did not name pideck-local.service"
      grep -q 'Command /usr/bin/caddy is not executable' "$out" || { printf '%s\n' '--- proxy output ---'; cat "$out"; fail "caddy was not the validation failure"; }
      grep -E -q 'Failed to prepare filename /tmp/tmp\.[^ :]+: Invalid argument' "$out" && fail "unnamed temp file was validated"
      grep -q 'daemon-reload' "$PIDECK_COMMAND_LOG" && fail "systemd was reloaded after proxy validation failed"
    fi
    ;;
  proxy-replaces-existing)
    cat > "$repo/ok.service" <<'EOF'
[Unit]
Description=PiDeck validation fixture
[Service]
Type=simple
ExecStart=/bin/true
[Install]
WantedBy=multi-user.target
EOF
    target="$PIDECK_FAKE_ROOT/etc/systemd/system/ok.service"
    mkdir -p "$(dirname "$target")"
    printf '%s\n' 'OLD-PROXY-UNIT' > "$target"
    install_checked_unit "$repo/ok.service" /etc/systemd/system/ok.service
    grep -q 'ExecStart=/bin/true' "$target" || fail "validated unit did not replace the installed file"
    grep -q 'OLD-PROXY-UNIT' "$target" && fail "old unit content remained"
    grep -q 'daemon-reload' "$PIDECK_COMMAND_LOG" || fail "daemon-reload did not run"
    grep -E -q 'verify-file 644 .*/ok\.service$' "$PIDECK_COMMAND_LOG" || fail "validator did not receive a readable ok.service"
    grep -E -q 'Failed to prepare filename /tmp/tmp\.' "$PIDECK_COMMAND_LOG" && fail "unnamed temp file was validated"
    ;;
  proxy-placeholder)
    printf '%s\n' '[Service]' 'ExecStart=__APP_DIR__/bin/true' > "$repo/bad.service"
    target="$PIDECK_FAKE_ROOT/etc/systemd/system/pideck-local.service"
    mkdir -p "$(dirname "$target")"
    printf '%s\n' 'OLD-PROXY-UNIT' > "$target"
    if install_checked_unit "$repo/bad.service" /etc/systemd/system/pideck-local.service; then
      fail "placeholder was installed"
    fi
    grep -qx 'OLD-PROXY-UNIT' "$target" || fail "placeholder replaced the installed unit"
    grep -q 'daemon-reload' "$PIDECK_COMMAND_LOG" && fail "daemon-reload ran for a placeholder"
    ;;
  *)
    fail "unknown mode $mode"
    ;;
esac
exit 0
"""


def _bash() -> Path | None:
    bash = Path(shutil.which("bash") or "")
    if not bash.is_file():
        return None
    probe = subprocess.run([str(bash), "-n", "-c", "true"], capture_output=True)
    if probe.returncode == 127:
        return None
    return bash


def _fixture(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    for relative in (
        "scripts/render-unit.py",
        "scripts/disconnect-client",
        "systemd/pideck.service",
        "systemd/pideck-terminal.service",
        "systemd/pideck-terminal.socket",
        "systemd/pideck-wifi.service",
        "systemd/pideck-wifi.socket",
        "systemd/pideck-discover.service",
        "systemd/pideck-discover.socket",
        "systemd/pideck-deauth.service",
        "systemd/pideck-deauth.socket",
        "systemd/pideck-local.service",
        "systemd/pideck.tmpfiles",
        "sudoers/pideck-discover",
        "sudoers/pideck-deauth",
        "polkit/50-pideck-wifi.rules",
    ):
        source = ROOT / relative
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    python = repo / "backend" / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    python.chmod(0o755)
    (repo / ".env").write_text("PIDECK_HOST=127.0.0.1\n", encoding="utf-8")
    (repo / "data").mkdir()
    return repo


def _run(mode: str, tmp_path: Path) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
    bash = _bash()
    if bash is None:
        pytest.skip("bash is not installed")
    if not Path("/etc/polkit-1/rules.d").is_dir():
        pytest.skip("polkit rules directory is required to exercise unit installation")
    if shutil.which("systemd-analyze") is None or shutil.which("visudo") is None:
        pytest.skip("systemd-analyze and visudo are required")
    repo = _fixture(tmp_path)
    dest = tmp_path / "installed"
    old = dest / "etc" / "systemd" / "system"
    old.mkdir(parents=True)
    (old / "pideck-discover.service").write_text(
        "[Service]\nPrivateTmp=yes\nExecStart=__APP_DIR__/backend/.venv/bin/python -m backend.discover_helper\n",
        encoding="utf-8",
    )
    log = tmp_path / "commands.log"
    harness = tmp_path / "harness.sh"
    harness.write_text(_HARNESS, encoding="utf-8", newline="\n")
    completed = subprocess.run(
        [str(bash), str(harness), str(ROOT / "scripts" / "ensure-accounts.sh"), str(repo), str(dest), str(log), mode, "python3"],
        capture_output=True,
        text=True,
    )
    return completed, log, dest


def test_deauth_sudoers_rule_is_literal_and_accepted_by_visudo(tmp_path: Path) -> None:
    path = ROOT / "sudoers" / "pideck-deauth"
    line = next(item for item in path.read_text(encoding="utf-8").splitlines() if item.startswith("pideck-deauth ALL="))
    assert "\\:" in line
    assert "*" not in path.read_text(encoding="utf-8")
    regex = subprocess.run(["grep", "-qx", line, str(path)], capture_output=True)
    fixed = subprocess.run(["grep", "-qxF", line, str(path)], capture_output=True)
    assert regex.returncode != 0
    assert fixed.returncode == 0
    accounts = (ROOT / "scripts" / "ensure-accounts.sh").read_text(encoding="utf-8")
    assert 'grep -qxF "$spec"' in accounts
    assert 'grep -qx "$spec"' not in accounts
    visudo = shutil.which("visudo")
    if visudo is None:
        pytest.skip("visudo is not installed")
    subprocess.run([visudo, "-cf", str(path)], check=True)
    broken = tmp_path / "unescaped.sudoers"
    broken.write_text(line.replace("\\:", ":") + "\n", encoding="utf-8")
    rejected = subprocess.run([visudo, "-cf", str(broken)], capture_output=True, text=True)
    assert rejected.returncode != 0


def test_rendered_discovery_unit_matches_the_working_pi_configuration() -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location("render_unit", ROOT / "scripts" / "render-unit.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    rendered = module.render_unit(
        (ROOT / "systemd" / "pideck-discover.service").read_text(encoding="utf-8"),
        Path("/home/admin/RasberryPI-Dashboard"),
        "pideck-discover",
        "pideck-discover",
    )
    assert "ExecStart=/home/admin/RasberryPI-Dashboard/backend/.venv/bin/python -m backend.discover_helper\n" in rendered
    assert "PrivateTmp=no\n" in rendered
    assert "ProtectSystem=strict\n" in rendered
    assert "ReadWritePaths=/tmp\n" in rendered
    assert "User=pideck-discover\n" in rendered
    assert "__APP_DIR__" not in rendered
    assert "__USER__" not in rendered
    assert "__GROUP__" not in rendered
    assert "PrivateTmp=yes" not in rendered


def test_update_reports_failure_when_a_required_helper_is_down() -> None:
    update = (ROOT / "scripts" / "update.sh").read_text(encoding="utf-8")
    install = (ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")
    migrate = (ROOT / "scripts" / "migrate-privileges.sh").read_text(encoding="utf-8")
    assert update.index("pideck_units_are_active") < update.index("PiDeck restarted successfully.")
    assert 'exec "$ROOT/scripts/update.sh"' in update
    assert install.index("pideck_units_are_active") < install.index("PiDeck is running as pideck.")
    assert migrate.index("pideck_units_are_active") < migrate.index("Health check succeeded.")
    for text in (update, migrate):
        commands = "\n".join(line for line in text.splitlines() if not line.strip().startswith("#"))
        for name in ("tailscale", "hostapd", "dnsmasq", "nft", "pihole", "NetworkManager"):
            assert name not in commands


def test_outdated_discovery_unit_is_replaced(tmp_path: Path) -> None:
    completed, log, _dest = _run("replace-old-unit", tmp_path)
    details = completed.stderr + completed.stdout
    if log.is_file():
        details += "\n--- log ---\n" + log.read_text(encoding="utf-8", errors="replace")
    assert completed.returncode == 0, details


def test_invalid_units_fail_before_installation(tmp_path: Path) -> None:
    completed, log, _dest = _run("reject-invalid", tmp_path)
    details = completed.stderr + completed.stdout
    if log.is_file():
        details += "\n--- log ---\n" + log.read_text(encoding="utf-8", errors="replace")
    assert completed.returncode == 0, details


def test_unnamed_temp_file_reproduces_systemd_analyze_invalid_argument() -> None:
    analyze = shutil.which("systemd-analyze")
    if analyze is None:
        pytest.skip("systemd-analyze is not installed")
    descriptor, bare = tempfile.mkstemp(prefix="tmp.")
    os.close(descriptor)
    try:
        assert re.fullmatch(r"/tmp/tmp\.[A-Za-z0-9]+", bare)
        Path(bare).write_text(
            (ROOT / "systemd" / "pideck-local.service").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        completed = subprocess.run([analyze, "verify", bare], capture_output=True, text=True)
    finally:
        os.unlink(bare)
    assert completed.returncode != 0
    assert f"Failed to prepare filename {bare}: Invalid argument" in completed.stderr


def test_proxy_unit_is_validated_under_its_systemd_name(tmp_path: Path) -> None:
    accounts = (ROOT / "scripts" / "ensure-accounts.sh").read_text(encoding="utf-8")
    function = accounts.split("install_checked_unit() {", 1)[1].split("\nprepare_caddy_state() {", 1)[0]
    assert 'staged="$(mktemp)"' not in function
    for mode in ("proxy-keeps-existing", "proxy-replaces-existing", "proxy-placeholder"):
        completed, log, _dest = _run(mode, tmp_path / mode)
        details = completed.stderr + completed.stdout
        if log.is_file():
            details += "\n--- log ---\n" + log.read_text(encoding="utf-8", errors="replace")
        assert completed.returncode == 0, details


def test_unit_validation_rejects_bad_files_and_returns_nonzero(tmp_path: Path) -> None:
    completed, log, _dest = _run("verify-rejects", tmp_path)
    details = completed.stderr + completed.stdout
    if log.is_file():
        details += "\n--- log ---\n" + log.read_text(encoding="utf-8", errors="replace")
    assert completed.returncode == 0, details
