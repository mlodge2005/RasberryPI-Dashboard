"""Dashboard-side client for the upstream Wi-Fi helper.

pideck does not run nmcli. It writes one request to the helper socket and
reads one response. The helper is a different account and only accepts the
operations implemented in wifi_nm.
"""

from __future__ import annotations

import json
import os
import socket
from typing import Protocol

from backend.services.wifi_nm import WifiError

PRODUCTION_SOCKET = "/run/pideck/wifi.sock"
_MAX_MESSAGE = 8192


class WifiBackend(Protocol):
    def call(self, op: str, body: dict[str, object]) -> dict[str, object]:
        """Run one helper operation and return its result object."""


def wifi_socket_path() -> str:
    configured = os.environ.get("PIDECK_WIFI_SOCKET", "").strip()
    if configured:
        return configured
    try:
        from backend.config import get_settings

        configured = get_settings().pideck_wifi_socket.strip()
    except Exception:
        configured = ""
    return configured or PRODUCTION_SOCKET


def _recv_exact(conn: socket.socket, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        piece = conn.recv(size - len(chunks))
        if not piece:
            raise WifiError("The Wi-Fi helper closed the connection.", status_code=503)
        chunks.extend(piece)
    return bytes(chunks)


def _read_message(conn: socket.socket) -> dict[str, object]:
    length = int.from_bytes(_recv_exact(conn, 4), "big")
    if length < 2 or length > _MAX_MESSAGE:
        raise WifiError("The Wi-Fi helper sent an unexpected response.", status_code=503)
    payload = json.loads(_recv_exact(conn, length))
    if not isinstance(payload, dict):
        raise WifiError("The Wi-Fi helper sent an unexpected response.", status_code=503)
    return payload


class SocketWifiBackend:
    def call(self, op: str, body: dict[str, object]) -> dict[str, object]:
        message = {"op": op, **body}
        raw = json.dumps(message).encode("utf-8")
        if len(raw) > _MAX_MESSAGE:
            raise WifiError("That request is too large.")
        timeout = 50.0 if op == "connect" else 30.0
        connection: socket.socket | None = None
        try:
            try:
                connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                connection.settimeout(timeout)
                connection.connect(wifi_socket_path())
            except (FileNotFoundError, ConnectionRefusedError, TimeoutError, OSError, AttributeError) as exc:
                raise WifiError("Upstream Wi-Fi control is unavailable.", status_code=503) from exc
            connection.sendall(len(raw).to_bytes(4, "big") + raw)
            payload = _read_message(connection)
        finally:
            if connection is not None:
                connection.close()
        if payload.get("ok") is True and isinstance(payload.get("result"), dict):
            return payload["result"]
        error = payload.get("error")
        status = payload.get("status_code")
        message_text = error if isinstance(error, str) and error else "The Wi-Fi helper could not complete that request."
        code = status if isinstance(status, int) and status in {400, 409, 503, 504} else 400
        raise WifiError(message_text, status_code=code)
