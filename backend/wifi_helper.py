"""Upstream Wi-Fi helper.

systemd starts this process as pideck-wifi and passes the listening socket.
The dashboard account connects and sends one JSON request. This process runs
fixed nmcli argument lists for wlan1 only. It has no shell and accepts no
interface name from the client.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import select
import socket
import subprocess
import sys
import threading
import time

from backend.services.wifi_nm import NMCLI_BIN, WifiError, command_is_allowed, execute, scrub

logger = logging.getLogger(__name__)
PRODUCTION_SOCKET = "/run/pideck/wifi.sock"
_MAX_MESSAGE = 8192
_LOCK = threading.Lock()


def is_production_socket(path: str) -> bool:
    normalized = os.path.abspath(path).replace("\\", "/").rstrip("/")
    return normalized == PRODUCTION_SOCKET or normalized.endswith("/run/pideck/wifi.sock")


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
    if names and names.split(":")[0] != "wifi":
        return None
    return 3


def _recv_exact(conn: socket.socket, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        piece = conn.recv(size - len(chunks))
        if not piece:
            raise WifiError("The request ended early.")
        chunks.extend(piece)
    return bytes(chunks)


def _send(conn: socket.socket, payload: dict[str, object]) -> None:
    raw = json.dumps(payload).encode("utf-8")
    if len(raw) > _MAX_MESSAGE:
        raw = json.dumps({"ok": False, "error": "The Wi-Fi response was too large.", "status_code": 500}).encode()
    conn.sendall(len(raw).to_bytes(4, "big") + raw)


def prompt_kind(buffer: bytes) -> str:
    """Classify nmcli's terminal prompt without treating every question as a passphrase."""
    text = buffer.decode("utf-8", "replace").replace("\r", "")
    lowered = text.lower()
    if "802-11-wireless-security.psk" in lowered:
        return "secret"
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    if lines and (lines[-1].endswith(":") or lines[-1].endswith("?")):
        return "other"
    return ""


def _nm_env() -> dict[str, str]:
    return {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C", "LANG": "C"}


def _argv(args: list[str], binary: list[str] | None) -> list[str]:
    if not command_is_allowed(args):
        raise WifiError("Refusing a Wi-Fi command that is not limited to the upstream adapter.")
    return [*(binary or [NMCLI_BIN]), *args[1:]]


def run_nmcli(args: list[str], timeout: float, binary: list[str] | None = None) -> str:
    command = _argv(args, binary)
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_nm_env(),
        )
    except subprocess.TimeoutExpired as exc:
        raise WifiError("The Wi-Fi operation timed out.", status_code=504) from exc
    except FileNotFoundError as exc:
        raise WifiError("Upstream Wi-Fi control is unavailable.", status_code=503) from exc
    if completed.returncode != 0:
        from backend.services.wifi_nm import public_nmcli_error

        detail = completed.stderr or completed.stdout or ""
        raise WifiError(public_nmcli_error(detail))
    return completed.stdout


def submit_secret(args: list[str], secret: str, timeout: float, binary: list[str] | None = None) -> str:
    """Give nmcli the passphrase on its terminal, never as a process argument."""
    if args[1:2] != ["--ask"]:
        raise WifiError("Refusing to send a Wi-Fi passphrase to that command.")
    if any(secret and secret in part for part in args):
        raise WifiError("Refusing to put a Wi-Fi passphrase on the command line.")
    command = _argv(args, binary)
    try:
        import pty
        import termios
    except ImportError as exc:
        raise WifiError("Upstream Wi-Fi control is unavailable.", status_code=503) from exc
    master, slave = pty.openpty()
    try:
        attributes = termios.tcgetattr(slave)
        attributes[3] = attributes[3] & ~(termios.ECHO | termios.ECHONL)
        termios.tcsetattr(slave, termios.TCSANOW, attributes)
    except termios.error:
        pass
    try:
        process = subprocess.Popen(
            command,
            stdin=slave,
            stdout=slave,
            stderr=slave,
            close_fds=True,
            start_new_session=True,
            env=_nm_env(),
        )
    except OSError as exc:
        os.close(master)
        os.close(slave)
        raise WifiError("Upstream Wi-Fi control is unavailable.", status_code=503) from exc
    os.close(slave)
    collected = bytearray()
    sent = False
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            if process.poll() is not None and not _read_ready(master):
                break
            if not _read_ready(master):
                continue
            try:
                chunk = os.read(master, 4096)
            except OSError:
                break
            if not chunk:
                break
            collected.extend(chunk)
            kind = prompt_kind(bytes(collected).replace(secret.encode("utf-8"), b""))
            if kind == "other" and not sent:
                process.kill()
                raise WifiError("NetworkManager asked an unexpected question.")
            if kind == "secret" and not sent:
                os.write(master, secret.encode("utf-8") + b"\n")
                sent = True
        else:
            process.kill()
            raise WifiError("The Wi-Fi operation timed out.", status_code=504)
        process.wait(timeout=5)
    finally:
        os.close(master)
    text = collected.decode("utf-8", "replace").replace(secret, "")
    if process.returncode != 0:
        from backend.services.wifi_nm import public_nmcli_error

        raise WifiError(public_nmcli_error(text, secret))
    return text


def _read_ready(fd: int) -> bool:
    ready, _, _ = select.select([fd], [], [], 0.2)
    return bool(ready)


def handle_payload(payload: dict[str, object], run) -> dict[str, object]:
    op = payload.get("op")
    if not isinstance(op, str):
        raise WifiError("That Wi-Fi operation is not available.")
    body = {key: value for key, value in payload.items() if key != "op"}
    secret = body.get("password")
    secret_text = secret if isinstance(secret, str) else ""
    if op == "status":
        acquired = _LOCK.acquire(blocking=True, timeout=50)
    else:
        acquired = _LOCK.acquire(blocking=False)
    if not acquired:
        raise WifiError("A Wi-Fi change is already running.", status_code=409)
    try:
        result = execute(op, body, run)
        return {"ok": True, "result": scrub(result, secret_text)}
    finally:
        _LOCK.release()


def handle_connection(conn: socket.socket, run) -> None:
    secret = ""
    try:
        length = int.from_bytes(_recv_exact(conn, 4), "big")
        if length < 2 or length > _MAX_MESSAGE:
            raise WifiError("That request is too large.")
        payload = json.loads(_recv_exact(conn, length))
        if not isinstance(payload, dict):
            raise WifiError("That Wi-Fi operation is not available.")
        password = payload.get("password")
        if isinstance(password, str):
            secret = password
        logger.info("Wi-Fi operation %s", payload.get("op") if isinstance(payload.get("op"), str) else "invalid")
        _send(conn, handle_payload(payload, run))
    except WifiError as exc:
        message = str(exc).replace(secret, "") if secret else str(exc)
        _send(conn, {"ok": False, "error": message, "status_code": exc.status_code})
    except (json.JSONDecodeError, UnicodeError, OSError):
        try:
            _send(conn, {"ok": False, "error": "The Wi-Fi helper could not read that request.", "status_code": 400})
        except OSError:
            pass
    except Exception:
        logger.exception("Wi-Fi helper failed")
        try:
            _send(conn, {"ok": False, "error": "The Wi-Fi helper could not complete that request.", "status_code": 503})
        except OSError:
            pass
    finally:
        try:
            conn.close()
        except OSError:
            pass


class NmcliRunner:
    def __init__(self, binary: list[str] | None = None) -> None:
        self.binary = binary

    def __call__(self, args: list[str], timeout: float) -> str:
        return run_nmcli(args, timeout, self.binary)

    def submit_secret(self, args: list[str], secret: str, timeout: float) -> str:
        return submit_secret(args, secret, timeout, self.binary)


def serve(listen_sock: socket.socket, run) -> None:
    listen_sock.setblocking(True)
    while True:
        try:
            connection, _address = listen_sock.accept()
        except OSError:
            return
        threading.Thread(target=handle_connection, args=(connection, run), name="pideck-wifi", daemon=True).start()


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
    parser = argparse.ArgumentParser(description="PiDeck upstream Wi-Fi helper")
    parser.add_argument("--socket", help="Test-only bind path. Refuses the production socket.")
    parser.add_argument("--nmcli-script", help="Test-only replacement for /usr/bin/nmcli.")
    args = parser.parse_args(argv)
    binary = [NMCLI_BIN]
    if args.nmcli_script:
        if not os.path.isfile(args.nmcli_script):
            logger.error("The test nmcli script does not exist")
            return 1
        binary = [sys.executable, os.path.abspath(args.nmcli_script)]

    runner = NmcliRunner(binary)
    inherited = inherited_listen_fd()
    if inherited is not None:
        os.set_inheritable(inherited, False)
        serve(socket.socket(fileno=inherited), runner)
        return 0
    if args.socket:
        if is_production_socket(args.socket):
            logger.error("Refusing to bind the production socket outside systemd")
            return 1
        serve(_bind_test_socket(args.socket), runner)
        return 0
    logger.error("No systemd socket activation and no --socket path")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
