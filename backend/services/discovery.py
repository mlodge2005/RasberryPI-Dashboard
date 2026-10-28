"""Collect hotspot devices and store their identities.

pideck reads the DHCP lease file and the wlan0 neighbor table directly.
Association status comes from the pideck-discover helper. A missing source
stays unavailable and is not treated as an empty client list.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from backend.database import utc_now
from backend.services.device_store import list_devices, save_devices
from backend.services.discovery_parse import (
    build_inventory,
    parse_dhcp_leases,
    parse_hostapd_stations,
    parse_neighbors,
    public_hostapd_error,
)
from backend.models.schemas import DeviceInventory

logger = logging.getLogger(__name__)
PRODUCTION_SOCKET = "/run/pideck/discover.sock"
LEASE_PATH = "/var/lib/misc/dnsmasq.leases"
_NEIGH_BINARIES = ("/usr/sbin/ip", "/sbin/ip", "/usr/bin/ip", "/bin/ip")
_NEIGH_ARGV_TAIL = ["-4", "neigh", "show", "dev", "wlan0"]
_MAX_MESSAGE = 262144
_LOCK = threading.Lock()
_ERROR_LOCK = threading.Lock()
_last_hostapd_error = ""
_KNOWN_REASONS = {"permission", "missing", "timeout", "hostapd", "socket", "unavailable"}
SOCKET_ERROR = "Association data is unavailable. The discovery helper is not running."
RESPONSE_ERROR = "Association data is unavailable. The discovery helper returned an unexpected response."


@dataclass(frozen=True)
class SourceSnapshot:
    hostapd: str | None
    dhcp: str | None
    neighbors: str | None
    hostapd_error: str = ""


class DeviceSources(Protocol):
    def read(self) -> SourceSnapshot:
        """Return hostapd, DHCP, and neighbor text. None means that source failed."""


def discover_socket_path() -> str:
    configured = os.environ.get("PIDECK_DISCOVER_SOCKET", "").strip()
    if configured:
        return configured
    try:
        from backend.config import get_settings

        configured = get_settings().pideck_discover_socket.strip()
    except Exception:
        configured = ""
    return configured or PRODUCTION_SOCKET


def _recv_exact(conn: socket.socket, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        piece = conn.recv(size - len(chunks))
        if not piece:
            raise OSError("closed")
        chunks.extend(piece)
    return bytes(chunks)


def _note_hostapd_error(message: str, reason: str = "") -> None:
    global _last_hostapd_error
    token = reason if reason in _KNOWN_REASONS else "unavailable"
    with _ERROR_LOCK:
        previous = _last_hostapd_error
        _last_hostapd_error = message
    if message == previous:
        return
    if message:
        logger.warning("Hotspot association unavailable (%s): %s", token, message)
        return
    logger.info("Hotspot association is available again")


def _helper_error(payload: dict[str, object]) -> tuple[str, str]:
    reason = payload.get("reason")
    token = reason if isinstance(reason, str) and reason in _KNOWN_REASONS else "unavailable"
    error = payload.get("error")
    if isinstance(error, str):
        return public_hostapd_error(error), token
    return RESPONSE_ERROR, token


def read_hostapd_observation() -> tuple[str | None, str, str]:
    """Return station text, a public error, and a short reason token.

    Text is None when association could not be read. An empty string is a
    successful read with no stations.
    """
    if not hasattr(socket, "AF_UNIX"):
        return None, SOCKET_ERROR, "socket"
    raw = json.dumps({"op": "stations"}).encode("utf-8")
    connection: socket.socket | None = None
    try:
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.settimeout(8)
        connection.connect(discover_socket_path())
        connection.sendall(len(raw).to_bytes(4, "big") + raw)
        length = int.from_bytes(_recv_exact(connection, 4), "big")
        if length < 2 or length > _MAX_MESSAGE:
            return None, RESPONSE_ERROR, "unavailable"
        payload = json.loads(_recv_exact(connection, length))
    except (OSError, TimeoutError, json.JSONDecodeError, UnicodeError, ValueError, AttributeError):
        return None, SOCKET_ERROR, "socket"
    finally:
        if connection is not None:
            connection.close()
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        if isinstance(payload, dict):
            message, reason = _helper_error(payload)
            return None, message, reason
        return None, RESPONSE_ERROR, "unavailable"
    result = payload.get("result")
    if isinstance(result, dict) and isinstance(result.get("text"), str):
        return result["text"], "", ""
    return None, RESPONSE_ERROR, "unavailable"


def read_hostapd_text() -> str | None:
    text, _error, _reason = read_hostapd_observation()
    return text


def read_lease_text(path: str = LEASE_PATH) -> str | None:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def read_neighbor_text() -> str | None:
    binary = next((item for item in _NEIGH_BINARIES if os.path.isfile(item)), None)
    if binary is None:
        return None
    try:
        completed = subprocess.run(
            [binary, *_NEIGH_ARGV_TAIL],
            capture_output=True,
            timeout=3,
            check=False,
            text=True,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout


class LiveSources:
    def read(self) -> SourceSnapshot:
        hostapd_text, hostapd_error, reason = read_hostapd_observation()
        if hostapd_text is None:
            _note_hostapd_error(hostapd_error, reason)
        else:
            _note_hostapd_error("")
        return SourceSnapshot(
            hostapd=hostapd_text,
            dhcp=read_lease_text(),
            neighbors=read_neighbor_text(),
            hostapd_error=hostapd_error,
        )


def refresh(sources: DeviceSources | None = None) -> DeviceInventory:
    reader = sources or LiveSources()
    with _LOCK:
        snapshot = reader.read()
        inventory, rows = build_inventory(
            list_devices(),
            parse_hostapd_stations(snapshot.hostapd) if snapshot.hostapd is not None else None,
            parse_dhcp_leases(snapshot.dhcp) if snapshot.dhcp is not None else None,
            parse_neighbors(snapshot.neighbors) if snapshot.neighbors is not None else None,
            utc_now().isoformat(),
            snapshot.hostapd_error,
        )
        save_devices(rows)
        return inventory
