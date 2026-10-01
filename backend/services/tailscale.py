"""Read Tailscale state with a fixed command list. Never from request input."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_CACHE_SECONDS = 15.0
_cache_lock = threading.Lock()
_cache: tuple[float, TailscaleInfo] | None = None


@dataclass(frozen=True)
class TailscaleInfo:
    status: str
    ip: str | None
    hostname: str | None


def get_tailscale_info() -> TailscaleInfo:
    global _cache
    now = time.monotonic()
    with _cache_lock:
        if _cache is not None and now - _cache[0] < _CACHE_SECONDS:
            return _cache[1]
    info = _query()
    with _cache_lock:
        _cache = (time.monotonic(), info)
    return info


def clear_tailscale_cache() -> None:
    global _cache
    with _cache_lock:
        _cache = None


def _query() -> TailscaleInfo:
    binary = shutil.which("tailscale")
    if binary is None:
        return TailscaleInfo(status="unavailable", ip=None, hostname=None)
    ip: str | None = None
    hostname: str | None = None
    online = False
    saw_status = False
    status_raw = _run([binary, "status", "--json"])
    if status_raw is not None and status_raw[1]:
        saw_status = True
        parsed = _parse_status(status_raw[1])
        if parsed is not None:
            ip, hostname, online = parsed
    if ip is None:
        ip_raw = _run([binary, "ip", "-4"])
        if ip_raw is not None and ip_raw[0] == 0 and ip_raw[1]:
            candidate = ip_raw[1].split()[0].strip()
            if "." in candidate and ":" not in candidate:
                ip = candidate
                online = True
                saw_status = True
    if not saw_status:
        return TailscaleInfo(status="unavailable", ip=None, hostname=None)
    status = "online" if online else "offline"
    return TailscaleInfo(status=status, ip=ip, hostname=hostname)


def _parse_status(raw: str) -> tuple[str | None, str | None, bool] | None:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    self_info = payload.get("Self")
    if not isinstance(self_info, dict):
        self_info = {}
    hostname = self_info.get("HostName")
    if not isinstance(hostname, str) or not hostname.strip():
        hostname = None
    ip = _first_ipv4(self_info.get("TailscaleIPs"))
    backend_state = str(payload.get("BackendState") or "")
    online = bool(self_info.get("Online")) and backend_state in {"", "Running"}
    if backend_state and backend_state != "Running":
        online = False
    return ip, hostname, online


def _first_ipv4(value: object) -> str | None:
    if not isinstance(value, list):
        return None
    for candidate in value:
        if isinstance(candidate, str) and "." in candidate and ":" not in candidate:
            return candidate
    return None


def _run(argv: list[str]) -> tuple[int, str] | None:
    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        logger.info("Tailscale command failed: %s", argv[1] if len(argv) > 1 else argv)
        return None
    return completed.returncode, completed.stdout.strip()
