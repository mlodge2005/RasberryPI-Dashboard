"""Interactive shell sessions.

Production Linux sessions are owned by the pideck-term helper. This process
only bridges bytes to its socket. Development on Linux can still open a local
PTY when PIDECK_DEV is true and the helper socket is absent. Windows keeps a
pipe-backed shell. There is no privilege escalation here.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import shutil
import socket
import subprocess
import sys
import threading
from collections import deque
from pathlib import Path
from typing import Protocol

from backend.services.terminal_protocol import (
    KIND_ERROR,
    KIND_EXIT,
    KIND_INPUT,
    KIND_OUTPUT,
    FrameReader,
    encode_frame,
    encode_resize,
)

logger = logging.getLogger(__name__)

MAX_MESSAGE_BYTES = 65536
_MAX_DIMENSION = 500


class ShellSession(Protocol):
    def read(self, timeout: float) -> bytes | None: ...

    def write(self, data: bytes) -> None: ...

    def resize(self, rows: int, cols: int) -> None: ...

    def close(self) -> None: ...


class TerminalSlots:
    def __init__(self, limit: int = 4) -> None:
        self.limit = limit
        self._counts: dict[int, int] = {}
        self._lock = threading.Lock()

    def acquire(self, user_id: int) -> bool:
        with self._lock:
            current = self._counts.get(user_id, 0)
            if current >= self.limit:
                return False
            self._counts[user_id] = current + 1
            return True

    def release(self, user_id: int) -> None:
        with self._lock:
            current = self._counts.get(user_id, 0)
            if current <= 1:
                self._counts.pop(user_id, None)
            else:
                self._counts[user_id] = current - 1


def build_shell(backend: str, helper_path: str) -> ShellSession:
    if backend == "windows":
        return WindowsPipeShell()
    if backend == "helper":
        return UnixHelperShell(helper_path)
    if backend == "local-pty":
        return LinuxPtyShell()
    raise RuntimeError("Terminal helper socket is not available")


def open_shell() -> ShellSession:
    path = terminal_socket_path()
    backend = shell_backend(
        platform=sys.platform,
        dev=dev_terminal_fallback(),
        helper_socket_present=os.path.exists(path),
    )
    return build_shell(backend, path)


def parse_client_message(text: str) -> dict[str, object]:
    if len(text.encode("utf-8")) > MAX_MESSAGE_BYTES:
        raise ValueError("message too large")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("invalid json") from exc
    if not isinstance(payload, dict):
        raise ValueError("expected object")
    kind = payload.get("type")
    if kind == "input":
        data = payload.get("data")
        if not isinstance(data, str):
            raise ValueError("bad input")
        if len(data.encode("utf-8")) > MAX_MESSAGE_BYTES:
            raise ValueError("input too large")
        return {"type": "input", "data": data}
    if kind == "resize":
        cols = payload.get("cols")
        rows = payload.get("rows")
        if isinstance(cols, bool) or isinstance(rows, bool):
            raise ValueError("bad resize")
        if not isinstance(cols, int) or not isinstance(rows, int):
            raise ValueError("bad resize")
        return {
            "type": "resize",
            "cols": max(1, min(cols, _MAX_DIMENSION)),
            "rows": max(1, min(rows, _MAX_DIMENSION)),
        }
    raise ValueError("unknown type")


def shell_environment() -> dict[str, str]:
    """Copy the service environment without PiDeck secrets.

    Development fallback only. The production helper does not use this.
    """
    env = os.environ.copy()
    for key in list(env):
        if key.upper().startswith("PIDECK_") or key == "CREDENTIALS_DIRECTORY":
            env.pop(key, None)
    env["TERM"] = "xterm-256color"
    env["COLORTERM"] = "truecolor"
    return env


_CHILD_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
_CHILD_KEYS = ("HOME", "USER", "LOGNAME", "SHELL", "PATH", "TERM", "COLORTERM", "LANG")


def terminal_child_environment() -> dict[str, str]:
    """Environment for the production shell. Nothing here comes from PiDeck."""
    import getpass

    account = getpass.getuser()
    return {
        "HOME": str(Path.home()),
        "USER": account,
        "LOGNAME": account,
        "SHELL": "/bin/bash",
        "PATH": _CHILD_PATH,
        "TERM": "xterm-256color",
        "COLORTERM": "truecolor",
        "LANG": "C.UTF-8",
    }


def dev_terminal_fallback() -> bool:
    if os.environ.get("CREDENTIALS_DIRECTORY", "").strip():
        return False
    raw = os.environ.get("PIDECK_DEV")
    if raw is not None and raw.strip() != "":
        return raw.strip().lower() in {"1", "true", "yes"}
    try:
        from backend.config import get_settings

        return bool(get_settings().pideck_dev)
    except Exception:
        return False


def terminal_socket_path() -> str:
    override = os.environ.get("PIDECK_TERMINAL_SOCKET", "").strip()
    if override:
        return override
    try:
        from backend.config import get_settings

        configured = get_settings().pideck_terminal_socket.strip()
    except Exception:
        configured = ""
    return configured or "/run/pideck/terminal.sock"


def shell_backend(*, platform: str, dev: bool, helper_socket_present: bool) -> str:
    """Choose windows, helper, local-pty, or unavailable. Never imply root."""
    if platform == "win32":
        return "windows"
    if helper_socket_present:
        return "helper"
    if dev:
        return "local-pty"
    return "unavailable"


def _home() -> str:
    return str(Path.home())


class LinuxPtyShell:
    def __init__(self, *, env: dict[str, str] | None = None, cwd: str | None = None) -> None:
        import fcntl
        import pty
        import termios

        shell = shutil.which("bash") or "/bin/bash"
        if not Path(shell).exists():
            fallback = shutil.which("sh") or "/bin/sh"
            shell = fallback
        master, slave = pty.openpty()
        self._master = master
        self._proc = subprocess.Popen(
            [shell, "--login", "-i"],
            stdin=slave,
            stdout=slave,
            stderr=slave,
            cwd=cwd or _home(),
            env=env if env is not None else shell_environment(),
            start_new_session=True,
            close_fds=True,
        )
        os.close(slave)
        flags = fcntl.fcntl(master, fcntl.F_GETFL)
        fcntl.fcntl(master, fcntl.F_SETFL, flags | os.O_NONBLOCK)
        self._termios = termios
        self._fcntl = fcntl
        self._closed = False

    def read(self, timeout: float) -> bytes | None:
        import errno
        import select

        if self._closed:
            return None
        try:
            ready, _, _ = select.select([self._master], [], [], timeout)
        except (OSError, ValueError):
            return None
        if not ready:
            if self._proc.poll() is not None:
                return None
            return b""
        try:
            data = os.read(self._master, 65536)
        except OSError as exc:
            if exc.errno in {errno.EIO, errno.EBADF}:
                return None
            if exc.errno == errno.EAGAIN:
                return b""
            return None
        if not data:
            return None
        return data

    def write(self, data: bytes) -> None:
        if self._closed or not data:
            return
        view = memoryview(data)
        while view:
            try:
                written = os.write(self._master, view)
            except OSError:
                return
            view = view[written:]

    def resize(self, rows: int, cols: int) -> None:
        import signal
        import struct

        if self._closed:
            return
        rows = max(1, min(rows, _MAX_DIMENSION))
        cols = max(1, min(cols, _MAX_DIMENSION))
        packed = struct.pack("HHHH", rows, cols, 0, 0)
        try:
            self._fcntl.ioctl(self._master, self._termios.TIOCSWINSZ, packed)
        except OSError:
            return
        if self._proc.poll() is None and self._proc.pid:
            try:
                os.killpg(self._proc.pid, signal.SIGWINCH)
            except OSError:
                return

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        import signal

        if self._proc.poll() is None and self._proc.pid:
            try:
                os.killpg(self._proc.pid, signal.SIGHUP)
            except OSError:
                self._proc.terminate()
            try:
                self._proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        try:
            os.close(self._master)
        except OSError:
            pass


_NEED_MORE = object()


class UnixHelperShell:
    """Client of the pideck-term helper. This process does not spawn a shell."""

    def __init__(self, path: str | None = None, connection: socket.socket | None = None) -> None:
        if connection is None:
            if not path:
                raise RuntimeError("Terminal helper socket is not available")
            connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            connection.settimeout(5)
            try:
                connection.connect(path)
            except OSError as exc:
                connection.close()
                raise RuntimeError("Terminal helper socket is not available") from exc
        self._sock = connection
        self._reader = FrameReader()
        self._queue: deque[tuple[int, bytes]] = deque()
        self._send_lock = threading.Lock()
        self._pending_exit = False
        self._closed = False

    def read(self, timeout: float) -> bytes | None:
        if self._closed:
            return None
        ready = self._take_output()
        if ready is not _NEED_MORE:
            return ready
        self._sock.settimeout(timeout if timeout and timeout > 0 else 0.01)
        try:
            data = self._sock.recv(65536)
        except TimeoutError:
            return b""
        except OSError:
            return None
        if not data:
            return None
        try:
            self._queue.extend(self._reader.feed(data))
        except ValueError:
            return None
        ready = self._take_output()
        if ready is _NEED_MORE:
            return b""
        return ready

    def _take_output(self) -> bytes | None | object:
        while self._queue:
            kind, payload = self._queue.popleft()
            if kind == KIND_OUTPUT:
                return payload
            if kind == KIND_ERROR:
                self._pending_exit = True
                return payload or b"terminal helper error"
            if kind == KIND_EXIT:
                self._pending_exit = True
                return None
        if self._pending_exit:
            return None
        return _NEED_MORE

    def write(self, data: bytes) -> None:
        if self._closed or not data:
            return
        view = memoryview(data)
        while view:
            chunk = bytes(view[:65536])
            view = view[len(chunk) :]
            self._send(encode_frame(KIND_INPUT, chunk))

    def resize(self, rows: int, cols: int) -> None:
        if self._closed:
            return
        self._send(encode_resize(rows, cols))

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._sock.close()
        except OSError:
            pass

    def _send(self, frame: bytes) -> None:
        with self._send_lock:
            try:
                self._sock.sendall(frame)
            except OSError:
                return


class WindowsPipeShell:
    """Development fallback. Full-screen programs need the Linux PTY."""

    def __init__(self) -> None:
        bash = shutil.which("bash")
        if bash:
            argv = [bash, "--login", "-i"]
        else:
            argv = ["powershell.exe", "-NoLogo", "-NoExit"]
        creationflags = 0
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            creationflags |= subprocess.CREATE_NO_WINDOW
        if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
            creationflags |= subprocess.CREATE_NEW_PROCESS_GROUP
        self._proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=_home(),
            env=shell_environment(),
            bufsize=0,
            creationflags=creationflags,
        )
        self._queue: queue.Queue[bytes | None] = queue.Queue()
        self._queue.put(
            b"\r\n[PiDeck] Windows development shell. Interactive programs such as htop need Linux.\r\n"
        )
        self._thread = threading.Thread(target=self._reader, name="pideck-shell", daemon=True)
        self._thread.start()
        self._closed = False

    def _reader(self) -> None:
        stdout = self._proc.stdout
        if stdout is None:
            self._queue.put(None)
            return
        try:
            while True:
                chunk = stdout.read(1024)
                if not chunk:
                    break
                self._queue.put(_normalize_newlines(chunk))
        except OSError:
            pass
        self._queue.put(None)

    def read(self, timeout: float) -> bytes | None:
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            if self._proc.poll() is not None:
                return None
            return b""

    def write(self, data: bytes) -> None:
        if self._closed or self._proc.stdin is None:
            return
        try:
            self._proc.stdin.write(data)
            self._proc.stdin.flush()
        except OSError:
            return

    def resize(self, rows: int, cols: int) -> None:
        del rows, cols

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        if self._proc.stdin is not None:
            try:
                self._proc.stdin.close()
            except OSError:
                pass


def _normalize_newlines(data: bytes) -> bytes:
    output = bytearray()
    previous = 0
    for byte in data:
        if byte == 10 and previous != 13:
            output.extend(b"\r\n")
        else:
            output.append(byte)
        previous = byte
    return bytes(output)
