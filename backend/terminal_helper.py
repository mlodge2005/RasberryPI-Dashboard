"""Listen on the systemd socket and open a shell as this process user.

The dashboard connects as `pideck`. This process is `pideck-term`. It execs a
fixed login shell and accepts no command, user, or environment from the client.
"""

from __future__ import annotations

import argparse
import logging
import os
import socket
import sys
import threading
from pathlib import Path

from backend.services.terminal_protocol import (
    KIND_ERROR,
    KIND_EXIT,
    KIND_INPUT,
    KIND_OUTPUT,
    KIND_RESIZE,
    FrameReader,
    decode_resize,
    encode_frame,
)

logger = logging.getLogger(__name__)
PRODUCTION_SOCKET = "/run/pideck/terminal.sock"


def is_production_socket(path: str) -> bool:
    normalized = os.path.abspath(path).replace("\\", "/").rstrip("/")
    return normalized == PRODUCTION_SOCKET or normalized.endswith("/run/pideck/terminal.sock")


class HelperSlots:
    def __init__(self, limit: int = 4) -> None:
        self.limit = limit
        self._count = 0
        self._lock = threading.Lock()

    def acquire(self) -> bool:
        with self._lock:
            if self._count >= self.limit:
                return False
            self._count += 1
            return True

    def release(self) -> None:
        with self._lock:
            self._count = max(0, self._count - 1)

    @property
    def in_use(self) -> int:
        with self._lock:
            return self._count


def inherited_listen_fd() -> int | None:
    try:
        listen_fds = int(os.environ.get("LISTEN_FDS", "0") or "0")
    except ValueError:
        return None
    if listen_fds < 1:
        return None
    if os.environ.get("LISTEN_PID", "") != str(os.getpid()):
        return None
    return 3


def default_opener():
    from backend.services.terminal import LinuxPtyShell, terminal_child_environment

    return LinuxPtyShell(env=terminal_child_environment(), cwd=str(Path.home()))


def _send_frame(conn: socket.socket, frame: bytes) -> bool:
    try:
        conn.sendall(frame)
        return True
    except OSError:
        return False


def _bridge(conn: socket.socket, shell) -> None:
    stop = threading.Event()

    def pump_output() -> None:
        try:
            while not stop.is_set():
                chunk = shell.read(0.25)
                if chunk is None:
                    _send_frame(conn, encode_frame(KIND_EXIT))
                    return
                view = memoryview(chunk)
                while view:
                    piece = bytes(view[:65536])
                    view = view[len(piece) :]
                    if piece and not _send_frame(conn, encode_frame(KIND_OUTPUT, piece)):
                        return
        finally:
            stop.set()
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def pump_input() -> None:
        reader = FrameReader()
        conn.settimeout(0.25)
        try:
            while not stop.is_set():
                try:
                    data = conn.recv(65536)
                except TimeoutError:
                    continue
                except OSError:
                    return
                if not data:
                    return
                try:
                    frames = reader.feed(data)
                except ValueError:
                    logger.info("Closing a terminal session after a bad frame")
                    return
                for kind, payload in frames:
                    if kind == KIND_INPUT:
                        shell.write(payload)
                    elif kind == KIND_RESIZE:
                        try:
                            rows, cols = decode_resize(payload)
                        except ValueError:
                            continue
                        shell.resize(rows, cols)
        finally:
            stop.set()

    output = threading.Thread(target=pump_output, name="pideck-term-out", daemon=True)
    incoming = threading.Thread(target=pump_input, name="pideck-term-in", daemon=True)
    output.start()
    incoming.start()
    incoming.join()
    stop.set()
    shell.close()
    output.join(timeout=2)


def handle_connection(conn: socket.socket, opener, slots: HelperSlots | None = None) -> None:
    limiter = slots if slots is not None else HelperSlots()
    if not limiter.acquire():
        _send_frame(conn, encode_frame(KIND_ERROR, b"Too many terminal sessions"))
        _send_frame(conn, encode_frame(KIND_EXIT))
        conn.close()
        return
    shell = None
    try:
        try:
            shell = opener()
        except Exception:
            logger.exception("Could not open a terminal session")
            _send_frame(conn, encode_frame(KIND_ERROR, b"Terminal session failed"))
            _send_frame(conn, encode_frame(KIND_EXIT))
            return
        logger.info("Terminal session opened")
        _bridge(conn, shell)
        logger.info("Terminal session closed")
    finally:
        if shell is not None:
            shell.close()
        limiter.release()
        try:
            conn.close()
        except OSError:
            pass


def serve(listen_sock: socket.socket, opener=default_opener) -> None:
    slots = HelperSlots()
    listen_sock.setblocking(True)
    while True:
        try:
            connection, _addr = listen_sock.accept()
        except OSError:
            return
        threading.Thread(
            target=handle_connection,
            args=(connection, opener, slots),
            name="pideck-term-session",
            daemon=True,
        ).start()


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    parser = argparse.ArgumentParser(description="PiDeck terminal helper")
    parser.add_argument("--socket", help="Test-only bind path. Refuses the production socket.")
    args = parser.parse_args(argv)
    inherited = inherited_listen_fd()
    if inherited is not None:
        os.set_inheritable(inherited, False)
        sock = socket.socket(fileno=inherited)
        serve(sock)
        return 0
    if args.socket:
        if is_production_socket(args.socket):
            logger.error("Refusing to bind the production socket outside systemd")
            return 1
        path = args.socket
        if os.path.exists(path):
            os.unlink(path)
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(path)
        try:
            os.chmod(path, 0o660)
        except OSError:
            pass
        sock.listen(8)
        serve(sock)
        return 0
    logger.error("No systemd socket activation and no --socket path")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
