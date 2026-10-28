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
    assert listeners == ["https://10.42.0.1:8443 {"]


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
