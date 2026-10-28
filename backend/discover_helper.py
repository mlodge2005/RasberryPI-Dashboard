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
import socket
import subprocess
import threading

logger = logging.getLogger(__name__)
PRODUCTION_SOCKET = "/run/pideck/discover.sock"
_MAX_MESSAGE = 262144
_MAX_OUTPUT = 200_000
ALLOWED_COMMANDS = (
    ["/usr/bin/sudo", "-n", "/usr/sbin/hostapd_cli", "-i", "wlan0", "all_sta"],
    ["/usr/bin/sudo", "-n", "/usr/bin/hostapd_cli", "-i", "wlan0", "all_sta"],
)


class DiscoverError(Exception):
    pass


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


def run_command(args: list[str], timeout: float) -> str:
    if args not in ALLOWED_COMMANDS:
        raise DiscoverError("Refusing a discovery command that is not association status.")
    if not os.path.isfile(args[0]) or not os.path.isfile(args[2]):
        raise DiscoverError("Association data is unavailable.")
    try:
        completed = subprocess.run(
            args,
            capture_output=True,
            timeout=timeout,
            check=False,
            text=True,
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C", "LANG": "C"},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DiscoverError("Association data is unavailable.") from exc
    if completed.returncode != 0:
        raise DiscoverError("Association data is unavailable.")
    return completed.stdout


def handle_payload(
    payload: object,
    run=run_command,
    command: list[str] | None = None,
) -> dict[str, object]:
    if not isinstance(payload, dict) or set(payload) != {"op"} or payload.get("op") != "stations":
        return {"ok": False, "error": "That discovery request is not available.", "status_code": 404}
    chosen = list(command) if command is not None else station_command()
    if chosen is None or chosen not in ALLOWED_COMMANDS:
        return {"ok": False, "error": "Association data is unavailable.", "status_code": 503}
    command = chosen
    try:
        text = run(command, 5.0)
    except DiscoverError:
        return {"ok": False, "error": "Association data is unavailable.", "status_code": 503}
    if not isinstance(text, str):
        return {"ok": False, "error": "Association data is unavailable.", "status_code": 503}
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
        logger.info("Discovery operation %s", op if isinstance(op, str) else "invalid")
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
            _send(conn, {"ok": False, "error": "Association data is unavailable.", "status_code": 503})
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
