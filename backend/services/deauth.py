"""Ask the disconnect helper to deauthenticate one associated client."""

from __future__ import annotations

import json
import logging
import os
import socket
from typing import Protocol

from backend.services.discovery_parse import unicast_mac

logger = logging.getLogger(__name__)
PRODUCTION_SOCKET = "/run/pideck/deauth.sock"
_MAX_MESSAGE = 8192
_KNOWN_REASONS = {"permission", "missing", "timeout", "hostapd", "mac", "socket", "unavailable"}
NOT_RUNNING = "Disconnect is unavailable. The disconnect helper is not running."
UNEXPECTED = "Disconnect is unavailable. The disconnect helper returned an unexpected response."


class DeauthError(Exception):
    def __init__(self, message: str, status_code: int = 503) -> None:
        super().__init__(message)
        self.status_code = status_code


class DeauthClient(Protocol):
    def disconnect(self, mac: str) -> None:
        """Deauthenticate one currently associated unicast client."""


def deauth_socket_path() -> str:
    configured = os.environ.get("PIDECK_DEAUTH_SOCKET", "").strip()
    if configured:
        return configured
    try:
        from backend.config import get_settings

        configured = get_settings().pideck_deauth_socket.strip()
    except Exception:
        configured = ""
    return configured or PRODUCTION_SOCKET


def _public_error(value: object, fallback: str) -> str:
    if not isinstance(value, str):
        return fallback
    text = " ".join(value.split())
    if len(text) > 200 or any(ord(char) < 32 or ord(char) == 127 for char in text):
        return fallback
    if text.startswith("Disconnect is unavailable") or text.startswith("That MAC address cannot"):
        return text
    return fallback


def _recv_exact(conn: socket.socket, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        piece = conn.recv(size - len(chunks))
        if not piece:
            raise OSError("closed")
        chunks.extend(piece)
    return bytes(chunks)


class SocketDeauth:
    def disconnect(self, mac: str) -> None:
        normalized = unicast_mac(mac)
        if normalized is None:
            raise DeauthError("That MAC address cannot be disconnected.", 400)
        if not hasattr(socket, "AF_UNIX"):
            raise DeauthError(NOT_RUNNING, 503)
        raw = json.dumps({"op": "disconnect", "mac": normalized}).encode("utf-8")
        connection: socket.socket | None = None
        try:
            connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            connection.settimeout(8)
            connection.connect(deauth_socket_path())
            connection.sendall(len(raw).to_bytes(4, "big") + raw)
            length = int.from_bytes(_recv_exact(connection, 4), "big")
            if length < 2 or length > _MAX_MESSAGE:
                raise DeauthError(UNEXPECTED, 503)
            payload = json.loads(_recv_exact(connection, length))
        except DeauthError:
            raise
        except (OSError, TimeoutError, json.JSONDecodeError, UnicodeError, ValueError, AttributeError) as exc:
            logger.warning("Disconnect helper unavailable: %s", type(exc).__name__)
            raise DeauthError(NOT_RUNNING, 503) from exc
        finally:
            if connection is not None:
                connection.close()
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            reason = payload.get("reason") if isinstance(payload, dict) else ""
            token = reason if isinstance(reason, str) and reason in _KNOWN_REASONS else "unavailable"
            message = _public_error(payload.get("error") if isinstance(payload, dict) else None, UNEXPECTED)
            status = payload.get("status_code") if isinstance(payload, dict) else 503
            code = status if isinstance(status, int) and status in {400, 503} else 503
            logger.warning("Disconnect helper refused the client (%s): %s", token, message)
            raise DeauthError(message, code)
        logger.info("Disconnect helper accepted one client")
