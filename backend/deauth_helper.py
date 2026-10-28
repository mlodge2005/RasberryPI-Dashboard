"""Disconnect one associated hotspot client.

systemd starts this process as pideck-deauth. It accepts one validated unicast
MAC and sudo-runs the root-owned disconnect program. It cannot choose an
interface, run hostapd_cli itself, or pass a broadcast address.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import socket
import subprocess
import threading

logger = logging.getLogger(__name__)
PRODUCTION_SOCKET = "/run/pideck/deauth.sock"
WRAPPER = "/usr/local/lib/pideck/disconnect-client"
_MAC = re.compile(r"^[0-9a-f]{2}(?::[0-9a-f]{2}){5}$")
_MAX_MESSAGE = 8192
_LOG_LOCK = threading.Lock()
_last_failure = ""
PERMISSION_ERROR = "Disconnect is unavailable. The disconnect account cannot run the disconnect program."
MISSING_ERROR = "Disconnect is unavailable. The disconnect program is not installed."
TIMEOUT_ERROR = "Disconnect is unavailable. hostapd did not answer before the timeout."
HOSTAPD_ERROR = "Disconnect is unavailable. hostapd did not deauthenticate that client."
GENERIC_ERROR = "Disconnect is unavailable."
MAC_ERROR = "That MAC address cannot be disconnected."


class DeauthHelperError(Exception):
    def __init__(self, message: str, reason: str = "unavailable", status_code: int = 503) -> None:
        super().__init__(message)
        self.reason = reason
        self.status_code = status_code


def command_args(raw: str) -> list[str] | None:
    """Same unicast rule as scripts/disconnect-client. The installed program checks again."""
    if not isinstance(raw, str) or raw != raw.strip() or any(char.isspace() for char in raw):
        return None
    mac = raw.lower().replace("-", ":")
    if mac != raw or _MAC.fullmatch(mac) is None:
        return None
    if int(mac[:2], 16) & 1:
        return None
    return ["/usr/sbin/hostapd_cli", "-i", "wlan0", "deauthenticate", mac]


def is_production_socket(path: str) -> bool:
    normalized = os.path.abspath(path).replace("\\", "/").rstrip("/")
    return normalized == PRODUCTION_SOCKET or normalized.endswith("/run/pideck/deauth.sock")


def inherited_listen_fd() -> int | None:
    try:
        listen_fds = int(os.environ.get("LISTEN_FDS", "0") or "0")
    except ValueError:
        return None
    if listen_fds < 1:
        return None
    if os.environ.get("LISTEN_PID", "") != str(os.getpid()):
        return None
    names = os.environ.get("LISTEN_FDNAMES", "")
    if names and names.split(":")[0] != "deauth":
        return None
    return 3


def disconnect_command(mac: str) -> list[str] | None:
    if command_args(mac) is None:
        return None
    return ["/usr/bin/sudo", "-n", WRAPPER, mac]


def command_is_allowed(args: list[str]) -> bool:
    if len(args) != 4:
        return False
    if args[:3] != ["/usr/bin/sudo", "-n", WRAPPER]:
        return False
    return disconnect_command(args[3]) == args


def sanitize_detail(text: str, limit: int = 240) -> str:
    cleaned = "".join(char if 32 <= ord(char) < 127 else " " for char in text)
    return " ".join(cleaned.split())[:limit]


def _remember_failure(message: str) -> None:
    global _last_failure
    with _LOG_LOCK:
        if message == _last_failure:
            return
        _last_failure = message
    logger.warning("%s", message)


def _failure_reason(stderr: str, stdout: str) -> str:
    blob = f"{stderr}\n{stdout}".lower()
    if any(
        token in blob
        for token in (
            "a password is required",
            "not allowed to execute",
            "not in the sudoers",
            "a terminal is required",
            "no tty present",
            "unable to allocate pty",
        )
    ):
        return "permission"
    if "refusing" in blob:
        return "mac"
    return "hostapd"


def _public_error(reason: str) -> str:
    if reason == "permission":
        return PERMISSION_ERROR
    if reason == "missing":
        return MISSING_ERROR
    if reason == "timeout":
        return TIMEOUT_ERROR
    if reason == "mac":
        return MAC_ERROR
    if reason == "hostapd":
        return HOSTAPD_ERROR
    return GENERIC_ERROR


def run_command(args: list[str], timeout: float) -> str:
    if not command_is_allowed(args):
        raise DeauthHelperError(MAC_ERROR, "mac", 400)
    if not os.path.isfile(args[0]) or not os.path.isfile(args[2]):
        _remember_failure("Disconnect command failed reason=missing detail=sudo or disconnect program is not installed")
        raise DeauthHelperError(MISSING_ERROR, "missing")
    try:
        completed = subprocess.run(
            args,
            capture_output=True,
            timeout=timeout,
            check=False,
            text=True,
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C", "LANG": "C"},
        )
    except subprocess.TimeoutExpired as exc:
        _remember_failure(f"Disconnect command failed reason=timeout detail=exceeded {timeout:.0f}s")
        raise DeauthHelperError(TIMEOUT_ERROR, "timeout") from exc
    except OSError as exc:
        _remember_failure(f"Disconnect command failed reason=unavailable detail={sanitize_detail(str(exc))}")
        raise DeauthHelperError(GENERIC_ERROR, "unavailable") from exc
    if completed.returncode == 0:
        global _last_failure
        with _LOG_LOCK:
            _last_failure = ""
        logger.info("Disconnected one hotspot client")
        return completed.stdout
    reason = _failure_reason(completed.stderr or "", completed.stdout or "")
    detail = sanitize_detail(completed.stderr or completed.stdout or "")
    _remember_failure(f"Disconnect command failed rc={completed.returncode} reason={reason} detail={detail}")
    status = 400 if reason == "mac" else 503
    raise DeauthHelperError(_public_error(reason), reason, status)


def handle_payload(payload: object, run=run_command) -> dict[str, object]:
    if not isinstance(payload, dict) or set(payload) != {"op", "mac"} or payload.get("op") != "disconnect":
        return {"ok": False, "error": "That disconnect request is not available.", "status_code": 404}
    mac = payload.get("mac")
    if not isinstance(mac, str):
        return {"ok": False, "error": MAC_ERROR, "reason": "mac", "status_code": 400}
    command = disconnect_command(mac)
    if command is None:
        return {"ok": False, "error": MAC_ERROR, "reason": "mac", "status_code": 400}
    try:
        run(command, 5.0)
    except DeauthHelperError as exc:
        return {"ok": False, "error": str(exc), "reason": exc.reason, "status_code": exc.status_code}
    return {"ok": True, "result": {"mac": command[-1]}}


def _recv_exact(conn: socket.socket, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        piece = conn.recv(size - len(chunks))
        if not piece:
            raise DeauthHelperError("The request ended early.", status_code=400)
        chunks.extend(piece)
    return bytes(chunks)


def _send(conn: socket.socket, payload: dict[str, object]) -> None:
    raw = json.dumps(payload).encode("utf-8")
    if len(raw) > _MAX_MESSAGE:
        raw = json.dumps({"ok": False, "error": "The disconnect response was too large.", "status_code": 500}).encode()
    conn.sendall(len(raw).to_bytes(4, "big") + raw)


def handle_connection(conn: socket.socket, run=run_command) -> None:
    try:
        length = int.from_bytes(_recv_exact(conn, 4), "big")
        if length < 2 or length > _MAX_MESSAGE:
            raise DeauthHelperError("The request was too large.", status_code=400)
        payload = json.loads(_recv_exact(conn, length))
        mac = payload.get("mac") if isinstance(payload, dict) else ""
        logger.info("Disconnect operation for %s", mac if isinstance(mac, str) and command_args(mac) else "rejected")
        _send(conn, handle_payload(payload, run))
    except DeauthHelperError:
        try:
            _send(conn, {"ok": False, "error": "The disconnect helper could not read that request.", "status_code": 400})
        except OSError:
            pass
    except (json.JSONDecodeError, UnicodeError, OSError):
        try:
            _send(conn, {"ok": False, "error": "The disconnect helper could not read that request.", "status_code": 400})
        except OSError:
            pass
    except Exception:
        logger.exception("Disconnect helper failed")
        try:
            _send(conn, {"ok": False, "error": GENERIC_ERROR, "reason": "unavailable", "status_code": 503})
        except OSError:
            pass
    finally:
        try:
            conn.close()
        except OSError:
            pass


def serve(listen_sock: socket.socket, run=run_command) -> None:
    listen_sock.setblocking(True)
    while True:
        try:
            connection, _address = listen_sock.accept()
        except OSError:
            return
        threading.Thread(
            target=handle_connection,
            args=(connection, run),
            name="pideck-deauth",
            daemon=True,
        ).start()


def _bind_test_socket(path: str) -> socket.socket:
    if os.path.exists(path):
        os.unlink(path)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.bind(path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    sock.listen(4)
    return sock


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    parser = argparse.ArgumentParser(description="PiDeck hotspot disconnect helper")
    parser.add_argument("--socket", help="Test-only bind path. Refuses the production socket.")
    args = parser.parse_args(argv)
    inherited = inherited_listen_fd()
    if inherited is not None:
        os.set_inheritable(inherited, False)
        serve(socket.socket(fileno=inherited))
        return 0
    if args.socket:
        if is_production_socket(args.socket):
            logger.error("Refusing to bind the production socket outside systemd")
            return 1
        serve(_bind_test_socket(args.socket))
        return 0
    logger.error("No systemd socket activation and no --socket path")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
