"""Collect hotspot devices and store their identities.

pideck reads the DHCP lease file and the wlan0 neighbor table directly.
Association status comes from the pideck-discover helper. A missing source
stays unavailable and is not treated as an empty client list.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
from pathlib import Path
from typing import Protocol

from backend.database import utc_now
from backend.services.device_store import list_devices, save_devices
from backend.services.discovery_parse import (
    build_inventory,
    parse_dhcp_leases,
    parse_hostapd_stations,
    parse_neighbors,
)
from backend.models.schemas import DeviceInventory

PRODUCTION_SOCKET = "/run/pideck/discover.sock"
LEASE_PATH = "/var/lib/misc/dnsmasq.leases"
_NEIGH_BINARIES = ("/usr/sbin/ip", "/sbin/ip", "/usr/bin/ip", "/bin/ip")
_NEIGH_ARGV_TAIL = ["-4", "neigh", "show", "dev", "wlan0"]
_MAX_MESSAGE = 262144
_LOCK = threading.Lock()


class DeviceSources(Protocol):
    def read(self) -> tuple[str | None, str | None, str | None]:
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


def read_hostapd_text() -> str | None:
    if not hasattr(socket, "AF_UNIX"):
        return None
    raw = json.dumps({"op": "stations"}).encode("utf-8")
    connection: socket.socket | None = None
    try:
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.settimeout(8)
        connection.connect(discover_socket_path())
        connection.sendall(len(raw).to_bytes(4, "big") + raw)
        length = int.from_bytes(_recv_exact(connection, 4), "big")
        if length < 2 or length > _MAX_MESSAGE:
            return None
        payload = json.loads(_recv_exact(connection, length))
    except (OSError, TimeoutError, json.JSONDecodeError, UnicodeError, ValueError, AttributeError):
        return None
    finally:
        if connection is not None:
            connection.close()
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        return None
    result = payload.get("result")
    if isinstance(result, dict) and isinstance(result.get("text"), str):
        return result["text"]
    return None


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
    def read(self) -> tuple[str | None, str | None, str | None]:
        return read_hostapd_text(), read_lease_text(), read_neighbor_text()


def refresh(sources: DeviceSources | None = None) -> DeviceInventory:
    reader = sources or LiveSources()
    with _LOCK:
        hostapd_text, lease_text, neighbor_text = reader.read()
        inventory, rows = build_inventory(
            list_devices(),
            parse_hostapd_stations(hostapd_text) if hostapd_text is not None else None,
            parse_dhcp_leases(lease_text) if lease_text is not None else None,
            parse_neighbors(neighbor_text) if neighbor_text is not None else None,
            utc_now().isoformat(),
        )
        save_devices(rows)
        return inventory
