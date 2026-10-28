"""Read-only hotspot association helper.

systemd starts this process as pideck-discover. The dashboard account asks for
current associations. The only privileged command is hostapd_cli reading
wlan0. The client cannot choose an interface, a station, or any other command.
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
PRODUCTION_SOCKET = "/run/pideck/discover.sock"
_MAX_MESSAGE = 262144
_MAX_OUTPUT = 200_000
ALLOWED_COMMANDS = (
    ["/usr/bin/sudo", "-n", "/usr/sbin/hostapd_cli", "-i", "wlan0", "all_sta"],
)
_MAC_LINE = re.compile(r"(?im)^[0-9a-f]{2}(?::[0-9a-f]{2}){5}\s*$")
_EMPTY_STATIONS = re.compile(r"^(?:selected interface '[^']+'\s+)?fail\s*$", re.IGNORECASE)
_LOG_LOCK = threading.Lock()
_last_failure = ""

PERMISSION_ERROR = "Association data is unavailable. The discovery account cannot read hostapd."
MISSING_ERROR = "Association data is unavailable. hostapd_cli is not installed."
TIMEOUT_ERROR = "Association data is unavailable. hostapd did not answer before the timeout."
HOSTAPD_ERROR = "Association data is unavailable. hostapd did not return association data."
GENERIC_ERROR = "Association data is unavailable."


class DiscoverError(Exception):
    def __init__(self, message: str, reason: str = "unavailable") -> None:
        super().__init__(message)
        self.reason = reason


def is_production_socket(path: str) -> bool:
    normalized = os.path.abspath(path).replace("\\", "/").rstrip("/")
    return normalized == PRODUCTION_SOCKET or normalized.endswith("/run/pideck/discover.sock")


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
    if names and names.split(":")[0] != "discover":
        return None
    return 3


def station_command() -> list[str] | None:
    """Return the one allowlisted command whose binary is installed."""
    for command in ALLOWED_COMMANDS:
        if os.path.isfile(command[2]):
            return list(command)
    return None


def sanitize_detail(text: str, limit: int = 240) -> str:
    cleaned = "".join(char if 32 <= ord(char) < 127 else " " for char in text)
    collapsed = " ".join(cleaned.split())
    return collapsed[:limit]


def _remember_failure(message: str) -> None:
    global _last_failure
    with _LOG_LOCK:
        if message == _last_failure:
            return
        _last_failure = message
    logger.warning("%s", message)


def _remember_success() -> None:
    global _last_failure
    with _LOG_LOCK:
        failed = _last_failure
        _last_failure = ""
    if failed:
        logger.info("Association command succeeded after a previous failure")


def failure_reason(stderr: str, stdout: str, returncode: int) -> str | None:
    """Return a failure reason, or None when stdout is a usable station list.

    hostapd_cli exits non-zero for an empty station list (`FAIL`) and also
    when it cannot reach hostapd. Only the latter is an association failure.
    """
    if returncode == 0:
        return None
    blob = f"{stderr}\n{stdout}".lower()
    if any(
        token in blob
        for token in (
            "a password is required",
            "not allowed to execute",
            "not permitted",
            "not in the sudoers",
            "a terminal is required",
            "no tty present",
            "unable to allocate pty",
            "authentication required",
        )
    ):
        return "permission"
    if any(token in blob for token in ("failed to connect", "wpa_ctrl", "connection refused", "permission denied")):
        return "hostapd"
    if "no such file" in blob and "hostapd" in blob:
        return "hostapd"
    if _MAC_LINE.search(stdout):
        return None
    # hostapd_cli prints FAIL when the station list is empty. Any other
    # non-zero exit, including a silent sudo failure, is not an empty list.
    if _EMPTY_STATIONS.match(stdout.strip()) and not stderr.strip():
        return None
    return "hostapd"


def _public_error(reason: str) -> str:
    if reason == "permission":
        return PERMISSION_ERROR
    if reason == "missing":
        return MISSING_ERROR
    if reason == "timeout":
        return TIMEOUT_ERROR
    if reason == "hostapd":
        return HOSTAPD_ERROR
    return GENERIC_ERROR


def run_command(args: list[str], timeout: float) -> str:
    if args not in ALLOWED_COMMANDS:
        raise DiscoverError("Refusing a discovery command that is not association status.")
    if not os.path.isfile(args[0]):
        _remember_failure("Association command failed reason=missing detail=sudo is not installed")
        raise DiscoverError(GENERIC_ERROR, "missing")
    if not os.path.isfile(args[2]):
        _remember_failure("Association command failed reason=missing detail=hostapd_cli is not installed")
        raise DiscoverError(MISSING_ERROR, "missing")
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
        _remember_failure(f"Association command failed reason=timeout detail=exceeded {timeout:.0f}s")
        raise DiscoverError(TIMEOUT_ERROR, "timeout") from exc
    except OSError as exc:
        _remember_failure(f"Association command failed reason=unavailable detail={sanitize_detail(str(exc))}")
        raise DiscoverError(GENERIC_ERROR, "unavailable") from exc
    reason = failure_reason(completed.stderr or "", completed.stdout or "", completed.returncode)
    if reason is None:
        _remember_success()
        if completed.returncode != 0:
            logger.info(
                "Association command returned no stations rc=%s",
                completed.returncode,
            )
        return completed.stdout
    detail = sanitize_detail(completed.stderr or completed.stdout or "")
    _remember_failure(
        f"Association command failed rc={completed.returncode} reason={reason} detail={detail}"
    )
    raise DiscoverError(_public_error(reason), reason)


def handle_payload(
    payload: object,
    run=run_command,
    command: list[str] | None = None,
) -> dict[str, object]:
    if not isinstance(payload, dict) or set(payload) != {"op"} or payload.get("op") != "stations":
        return {"ok": False, "error": "That discovery request is not available.", "status_code": 404}
    chosen = list(command) if command is not None else station_command()
    if chosen is None or chosen not in ALLOWED_COMMANDS:
        _remember_failure("Association command failed reason=missing detail=allowlisted hostapd_cli is not installed")
        return {"ok": False, "error": MISSING_ERROR, "reason": "missing", "status_code": 503}
    try:
        text = run(chosen, 5.0)
    except DiscoverError as exc:
        return {"ok": False, "error": str(exc), "reason": exc.reason, "status_code": 503}
    if not isinstance(text, str):
        return {"ok": False, "error": GENERIC_ERROR, "reason": "unavailable", "status_code": 503}
    return {"ok": True, "result": {"text": text[:_MAX_OUTPUT]}}


def _recv_exact(conn: socket.socket, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        piece = conn.recv(size - len(chunks))
        if not piece:
            raise DiscoverError("The request ended early.")
        chunks.extend(piece)
    return bytes(chunks)


def _send(conn: socket.socket, payload: dict[str, object]) -> None:
    raw = json.dumps(payload).encode("utf-8")
    if len(raw) > _MAX_MESSAGE:
        raw = json.dumps(
            {"ok": False, "error": "The discovery response was too large.", "status_code": 500}
        ).encode()
    conn.sendall(len(raw).to_bytes(4, "big") + raw)


def handle_connection(conn: socket.socket, run=run_command) -> None:
    try:
        length = int.from_bytes(_recv_exact(conn, 4), "big")
        if length < 2 or length > _MAX_MESSAGE:
            raise DiscoverError("The request was too large.")
        payload = json.loads(_recv_exact(conn, length))
        op = payload.get("op") if isinstance(payload, dict) else "invalid"
        logger.info("Discovery operation %s", op if isinstance(op, str) and op.isidentifier() else "invalid")
        _send(conn, handle_payload(payload, run))
    except DiscoverError:
        try:
            _send(conn, {"ok": False, "error": "The discovery helper could not read that request.", "status_code": 400})
        except OSError:
            pass
    except (json.JSONDecodeError, UnicodeError, OSError):
        try:
            _send(conn, {"ok": False, "error": "The discovery helper could not read that request.", "status_code": 400})
        except OSError:
            pass
    except Exception:
        logger.exception("Discovery helper failed")
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
            name="pideck-discover",
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
    parser = argparse.ArgumentParser(description="PiDeck hotspot discovery helper")
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
