from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.datastructures import Headers
from starlette.requests import Request

from backend.tests.constants import PASSWORD

ROOT = Path(__file__).resolve().parents[2]
HOTSPOT = "10.42.0.1:8443"


def _commands(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.strip().startswith("#"))


def test_caddyfile_binds_only_the_hotspot_address() -> None:
    text = (ROOT / "caddy" / "Caddyfile").read_text(encoding="utf-8")
    assert "auto_https off" in text
    assert "https://10.42.0.1:8443 {" in text
    assert "tls /var/lib/pideck-local/cert.pem /var/lib/pideck-local/key.pem" in text
    assert "reverse_proxy 127.0.0.1:8080" in text
    assert "header_up X-Forwarded-Proto https" in text
    assert "header_up X-Forwarded-Host {host}" in text
    assert "header_up Host" not in text
    assert "0.0.0.0" not in text
    assert "wlan1" not in text
    listeners = [line.strip() for line in text.splitlines() if "://" in line or line.strip().startswith("bind ")]
    assert listeners == ["https://10.42.0.1:8443 {", "bind 10.42.0.1"]
    assert "*:8443" not in text
    assert "bind *" not in text


def test_local_proxy_unit_is_independent_of_the_dashboard() -> None:
    unit = (ROOT / "systemd" / "pideck-local.service").read_text(encoding="utf-8")
    dashboard = (ROOT / "systemd" / "pideck.service").read_text(encoding="utf-8")
    assert "User=caddy\n" in unit
    assert "Group=caddy\n" in unit
    assert "/etc/pideck/Caddyfile" in unit
    assert "Restart=on-failure\n" in unit
    assert "BindsTo=" not in unit
    assert "PartOf=" not in unit
    assert "Requires=pideck.service" not in unit
    assert "PrivateNetwork" not in unit
    assert "AmbientCapabilities" not in unit
    assert "Environment=HOME=/var/lib/pideck-local/state\n" in unit
    assert "ReadWritePaths=/var/lib/pideck-local/state\n" in unit
    assert "pideck-local" not in dashboard
    assert "0.0.0.0" not in unit


def test_local_https_scripts_do_not_touch_other_services() -> None:
    install = (ROOT / "scripts" / "install-local-https.sh").read_text(encoding="utf-8")
    rollback = (ROOT / "scripts" / "rollback-local-https.sh").read_text(encoding="utf-8")
    update = (ROOT / "scripts" / "update.sh").read_text(encoding="utf-8")
    install_commands = _commands(install)
    rollback_commands = _commands(rollback)
    for name in ("tailscale", "hostapd", "dnsmasq", "nft", "pihole", "NetworkManager"):
        assert name not in install_commands
        assert name not in rollback_commands
    assert "chown -R" not in install
    assert "systemctl disable --now caddy.service" in install_commands
    assert "systemctl restart pideck-local.service" in install_commands
    assert "systemctl restart pideck.service" not in install_commands
    assert "systemctl stop pideck" not in install_commands
    assert "--confirm-remove-local-proxy" in rollback
    assert "disable --now pideck-local.service" in rollback_commands
    assert "systemctl enable" not in rollback_commands
    assert "systemctl restart pideck" not in rollback_commands
    assert '[[ -f /etc/systemd/system/pideck-local.service ]]' in update
    assert "prepare_caddy_state" in update
    assert update.index("prepare_caddy_state") < update.index("systemctl restart pideck-local.service")
    assert "prepare_caddy_state" in install
    assert "systemctl restart pideck-local.service" in update


def test_hotspot_origin_and_secure_cookie(client: TestClient, password_hash: str) -> None:
    from backend.config import get_settings
    from backend.database import create_user
    from backend.utils.security import cookie_is_secure, origin_allowed

    hotspot = Headers({"origin": f"https://{HOTSPOT}", "host": HOTSPOT})
    assert origin_allowed(hotspot)
    tailnet = Headers(
        {
            "origin": "https://cyberdeckv2.tail1eaaa2.ts.net",
            "host": "cyberdeckv2.tail1eaaa2.ts.net",
        }
    )
    assert origin_allowed(tailnet)
    assert not origin_allowed(Headers({"origin": f"https://{HOTSPOT}", "host": "192.168.1.50"}))
    assert not origin_allowed(Headers({"origin": "https://evil.example", "host": HOTSPOT}))

    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "headers": [
            (b"host", HOTSPOT.encode()),
            (b"x-forwarded-proto", b"https"),
        ],
        "client": ("127.0.0.1", 1234),
        "server": ("127.0.0.1", 8080),
    }
    assert cookie_is_secure(Request(scope), get_settings())

    create_user("admin", password_hash)
    headers = {
        "Origin": f"https://{HOTSPOT}",
        "Host": HOTSPOT,
        "X-Forwarded-Proto": "https",
    }
    response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": PASSWORD},
        headers=headers,
    )
    assert response.status_code == 200
    cookie = response.headers["set-cookie"].lower()
    assert "secure" in cookie
    assert "httponly" in cookie
    session = response.cookies.get("pideck_session")
    assert session
    logged_out = client.post(
        "/api/auth/logout",
        headers=headers,
        cookies={"pideck_session": session},
    )
    assert logged_out.status_code == 200


def test_frontend_and_caddy_storage_repairs_are_narrow() -> None:
    accounts = (ROOT / "scripts" / "ensure-accounts.sh").read_text(encoding="utf-8")
    repair = accounts.split("repair_frontend_build_ownership()", 1)[1].split("\npublish_frontend_dist()", 1)[0]
    state = accounts.split("prepare_caddy_state()", 1)[1]
    assert repair.count("chown -R") == 1
    assert '"$dir"' in repair
    assert "frontend/node_modules" in repair
    assert "frontend/dist" in repair
    assert "Refusing to repair frontend ownership as root." in repair
    for line in repair.splitlines():
        if "chown" in line:
            assert "$dir" in line
            assert "$root\"" not in line
    assert "chmod 755" in accounts
    assert "chmod 644" in accounts
    assert "chown caddy:caddy /var/lib/pideck-local/state" in state
    assert "ca.key" not in state
    dashboard_install = (ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")
    update = (ROOT / "scripts" / "update.sh").read_text(encoding="utf-8")
    for text in (dashboard_install, update):
        assert "sudo npm" not in text
        assert text.index("repair_frontend_build_ownership") < text.index("npm ci")
        assert text.index("npm run build") < text.index("publish_frontend_dist")


_REPAIR_HARNESS = r"""
set -euo pipefail
source "$1"
log="$2"
mode="$3"
: > "$log"
FIND_FOREIGN=1
MOCK_USER=admin
MOCK_GROUP=admin
id() {
  if [[ "${1:-}" == "caddy" ]]; then
    return 0
  fi
  case "${1:-}" in
    -un) printf '%s\n' "$MOCK_USER" ;;
    -gn) printf '%s\n' "$MOCK_GROUP" ;;
    *) return 1 ;;
  esac
}
sudo() {
  printf 'sudo %s\n' "$*" >> "$log"
  if [[ "$1" == "find" && "$2" == *node_modules && "$FIND_FOREIGN" == "1" ]]; then
    printf '%s\n' "$2/root-owned"
  fi
}
fail() { printf '%s\n' "$1" >&2; exit 1; }

case "$mode" in
  repair)
    repo=/tmp/pideck-fe-$$
    rm -rf "$repo"
    mkdir -p "$repo/frontend/node_modules" "$repo/frontend/dist" "$repo/data"
    repair_frontend_build_ownership "$repo"
    grep -q "sudo chown -R -- admin:admin $repo/frontend/node_modules$" "$log" || fail "node_modules was not repaired"
    grep -q "sudo chown -R -- admin:admin $repo/frontend/dist$" "$log" && fail "already-owned dist was reowned"
    grep -q "sudo chown -R -- admin:admin $repo$" "$log" && fail "repository root was reowned"
    grep -q "$repo/data" "$log" && fail "database directory was included"
    FIND_FOREIGN=0
    : > "$log"
    repair_frontend_build_ownership "$repo"
    grep -q "chown -R" "$log" && fail "second repair changed ownership"
    MOCK_USER=root
    if repair_frontend_build_ownership "$repo"; then
      fail "root was allowed to repair ownership"
    fi
    grep -q "chown" "$log" && fail "root repair changed ownership"
    rm -rf "$repo"
    ;;
  caddy-state)
    prepare_caddy_state
    grep -qx "sudo mkdir -p /var/lib/pideck-local/state" "$log" || fail "state directory was not created"
    grep -qx "sudo chown caddy:caddy /var/lib/pideck-local/state" "$log" || fail "state directory owner is wrong"
    grep -qx "sudo chmod 750 /var/lib/pideck-local/state" "$log" || fail "state directory mode is wrong"
    grep -qx "sudo chown caddy:caddy /var/lib/pideck-local" "$log" && fail "certificate directory was given to caddy"
    grep -q "ca.key" "$log" && fail "CA key was included"
    grep -q "chown -R" "$log" && fail "state ownership change was recursive"
    ;;
  *)
    fail "unknown mode $mode"
    ;;
esac
exit 0
"""


@pytest.mark.parametrize("mode", ["repair", "caddy-state"])
def test_frontend_repair_and_caddy_state_behavior(tmp_path: Path, mode: str) -> None:
    bash = _bash()
    if bash is None:
        pytest.skip("bash is not installed")
    harness = tmp_path / "harness.sh"
    log = tmp_path / "commands.log"
    harness.write_text(_REPAIR_HARNESS, encoding="utf-8", newline="\n")
    completed = subprocess.run(
        [str(bash), str(harness), str(ROOT / "scripts" / "ensure-accounts.sh"), str(log), mode],
        capture_output=True,
        text=True,
    )
    details = completed.stderr + completed.stdout
    if log.is_file():
        details += "\n--- log ---\n" + log.read_text(encoding="utf-8", errors="replace")
    assert completed.returncode == 0, details


def _bash() -> Path | None:
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


def _openssl_dir() -> Path | None:
    candidates = [
        Path(r"C:\Program Files\Git\usr\bin\openssl.exe"),
        Path(shutil.which("openssl") or ""),
    ]
    openssl = next((path for path in candidates if path.is_file()), None)
    return openssl.parent if openssl else None


def test_local_certificate_is_idempotent_and_rejects_all_interfaces(tmp_path: Path) -> None:
    bash = _bash()
    openssl_dir = _openssl_dir()
    if bash is None or openssl_dir is None:
        pytest.skip("bash and openssl are required")
    script = ROOT / "scripts" / "local-https-cert.sh"
    env = dict(os.environ)
    env["PATH"] = str(openssl_dir) + os.pathsep + env.get("PATH", "")
    rejected = subprocess.run(
        [str(bash), str(script), "--dir", str(tmp_path / "rejected"), "--ip", "0.0.0.0"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert rejected.returncode != 0
    assert not (tmp_path / "rejected" / "ca.key").exists()

    destination = tmp_path / "certs"
    first = subprocess.run(
        [str(bash), str(script), "--dir", str(destination), "--ip", "10.42.0.1"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert first.returncode == 0, first.stderr
    assert "certificate status: created" in first.stdout
    ca = (destination / "ca.crt").read_bytes()
    leaf_key = (destination / "key.pem").read_bytes()
    verified = subprocess.run(
        [
            str(openssl_dir / "openssl.exe") if (openssl_dir / "openssl.exe").exists() else "openssl",
            "verify",
            "-CAfile",
            str(destination / "ca.crt"),
            str(destination / "cert.pem"),
        ],
        capture_output=True,
        text=True,
    )
    assert verified.returncode == 0, verified.stderr
    text = subprocess.run(
        [
            str(openssl_dir / "openssl.exe") if (openssl_dir / "openssl.exe").exists() else "openssl",
            "x509",
            "-in",
            str(destination / "cert.pem"),
            "-noout",
            "-ext",
            "subjectAltName",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "10.42.0.1" in text.stdout
    second = subprocess.run(
        [str(bash), str(script), "--dir", str(destination), "--ip", "10.42.0.1"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert second.returncode == 0, second.stderr
    assert "certificate status: kept-existing" in second.stdout
    assert (destination / "ca.crt").read_bytes() == ca
    assert (destination / "key.pem").read_bytes() == leaf_key
    (destination / "cert.pem").unlink()
    renewed = subprocess.run(
        [str(bash), str(script), "--dir", str(destination), "--ip", "10.42.0.1"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert renewed.returncode == 0, renewed.stderr
    assert "certificate status: renewed-leaf" in renewed.stdout
    assert (destination / "ca.crt").read_bytes() == ca
