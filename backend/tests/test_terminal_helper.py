from __future__ import annotations

import os
import socket
import sys
import threading

import pytest

from backend.services.terminal import UnixHelperShell
from backend.terminal_helper import HelperSlots, handle_connection, inherited_listen_fd, is_production_socket, main


class FakeShell:
    def __init__(self) -> None:
        self.written: list[bytes] = []
        self.size: tuple[int, int] | None = None
        self.closed = False
        self._banner_sent = False
        self._closed_event = threading.Event()

    def read(self, timeout: float) -> bytes | None:
        if not self._banner_sent:
            self._banner_sent = True
            return b"ready"
        if self._closed_event.wait(timeout):
            return None
        return b""

    def write(self, data: bytes) -> None:
        self.written.append(data)

    def resize(self, rows: int, cols: int) -> None:
        self.size = (rows, cols)

    def close(self) -> None:
        self.closed = True
        self._closed_event.set()


def test_helper_session_cleans_up_the_shell() -> None:
    left, right = socket.socketpair()
    shells: list[FakeShell] = []

    def opener() -> FakeShell:
        shell = FakeShell()
        shells.append(shell)
        return shell

    slots = HelperSlots()
    worker = threading.Thread(target=handle_connection, args=(right, opener, slots), daemon=True)
    worker.start()
    client = UnixHelperShell(connection=left)
    assert client.read(2) == b"ready"
    client.write(b"echo hi\n")
    client.resize(24, 80)
    deadline = threading.Event()
    for _ in range(100):
        if shells and shells[0].written and shells[0].size is not None:
            break
        deadline.wait(0.02)
    client.close()
    worker.join(3)
    assert not worker.is_alive()
    assert shells[0].written == [b"echo hi\n"]
    assert shells[0].size == (24, 80)
    assert shells[0].closed is True
    assert slots.in_use == 0


def test_helper_does_not_open_a_shell_when_full() -> None:
    left, right = socket.socketpair()

    def opener() -> FakeShell:
        raise AssertionError("shell should not start")

    handle_connection(right, opener, HelperSlots(limit=0))
    client = UnixHelperShell(connection=left)
    assert client.read(2) == b"Too many terminal sessions"
    assert client.read(2) is None
    client.close()


def test_helper_releases_a_slot_when_open_fails() -> None:
    left, right = socket.socketpair()

    def opener() -> FakeShell:
        raise RuntimeError("boom")

    slots = HelperSlots()
    handle_connection(right, opener, slots)
    assert slots.in_use == 0
    client = UnixHelperShell(connection=left)
    assert client.read(2) == b"Terminal session failed"
    client.close()


def test_inherited_socket_requires_this_process(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LISTEN_FDS", "1")
    monkeypatch.setenv("LISTEN_PID", "1")
    assert inherited_listen_fd() is None
    monkeypatch.setenv("LISTEN_PID", str(os.getpid()))
    assert inherited_listen_fd() == 3


def test_helper_refuses_to_bind_the_production_socket() -> None:
    assert is_production_socket("/run/pideck/terminal.sock") is True
    assert main(["--socket", "/run/pideck/terminal.sock"]) == 1


@pytest.mark.skipif(sys.platform != "linux", reason="Linux PTY and /proc environ")
def test_linux_pty_child_has_no_secret_and_closes(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from backend.services.terminal import LinuxPtyShell, terminal_child_environment

    monkeypatch.setenv("PIDECK_SESSION_SECRET", "super-secret-value")
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", "/run/credentials/pideck.service")
    shell = LinuxPtyShell(env=terminal_child_environment(), cwd=str(tmp_path))
    try:
        pid = shell._proc.pid
        raw = open(f"/proc/{pid}/environ", "rb").read()
        assert b"PIDECK_SESSION_SECRET" not in raw
        assert b"super-secret-value" not in raw
        assert b"CREDENTIALS_DIRECTORY" not in raw
    finally:
        shell.close()
    assert shell._proc.poll() is not None
