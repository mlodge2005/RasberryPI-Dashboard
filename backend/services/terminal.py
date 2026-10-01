"""Interactive shell sessions.

Linux uses a PTY so programs such as vim and htop work. Windows development
gets a pipe-backed shell, which is not a terminal. The shell always starts as
the account that launched PiDeck. There is no privilege escalation here.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Protocol

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


def open_shell() -> ShellSession:
    if sys.platform == "win32":
        return WindowsPipeShell()
    return LinuxPtyShell()


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
    """Copy the service environment without PiDeck secrets."""
    env = os.environ.copy()
    for key in list(env):
        if key.upper().startswith("PIDECK_"):
            env.pop(key, None)
    env["TERM"] = "xterm-256color"
    env["COLORTERM"] = "truecolor"
    return env


def _home() -> str:
    return str(Path.home())


class LinuxPtyShell:
    def __init__(self) -> None:
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
            cwd=_home(),
            env=shell_environment(),
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
