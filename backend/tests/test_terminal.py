from __future__ import annotations

import json
import threading
import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect


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


def test_terminal_rejects_anonymous_without_spawning(client: TestClient) -> None:
    calls = {"count": 0}

    def factory() -> FakeShell:
        calls["count"] += 1
        return FakeShell()

    client.app.state.shell_factory = factory
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/terminal", headers={"origin": "http://testserver"}) as socket:
            socket.receive_text()
    assert calls["count"] == 0


def test_terminal_rejects_bad_origin_without_spawning(auth_client: TestClient) -> None:
    calls = {"count": 0}

    def factory() -> FakeShell:
        calls["count"] += 1
        return FakeShell()

    auth_client.app.state.shell_factory = factory
    with pytest.raises(WebSocketDisconnect):
        with auth_client.websocket_connect(
            "/ws/terminal",
            headers={"origin": "https://evil.example"},
        ) as socket:
            socket.receive_text()
    assert calls["count"] == 0


def test_authenticated_terminal_uses_pty_bridge(auth_client: TestClient) -> None:
    shells: list[FakeShell] = []

    def factory() -> FakeShell:
        shell = FakeShell()
        shells.append(shell)
        return shell

    auth_client.app.state.shell_factory = factory
    with auth_client.websocket_connect("/ws/terminal", headers={"origin": "http://testserver"}) as socket:
        assert socket.receive_bytes() == b"ready"
        socket.send_text(json.dumps({"type": "input", "data": "echo hi\n"}))
        socket.send_text(json.dumps({"type": "resize", "cols": 120, "rows": 40}))
        deadline = time.time() + 2
        while time.time() < deadline and (not shells[0].written or shells[0].size is None):
            time.sleep(0.02)
    assert shells[0].written == [b"echo hi\n"]
    assert shells[0].size == (40, 120)
    deadline = time.time() + 2
    while time.time() < deadline and not shells[0].closed:
        time.sleep(0.02)
    assert shells[0].closed is True


def test_parse_client_message_limits() -> None:
    from backend.services.terminal import parse_client_message

    assert parse_client_message('{"type":"input","data":"ls"}') == {"type": "input", "data": "ls"}
    resized = parse_client_message('{"type":"resize","cols":10000,"rows":0}')
    assert resized == {"type": "resize", "cols": 500, "rows": 1}
    with pytest.raises(ValueError):
        parse_client_message('{"type":"run","data":"rm -rf /"}')
    with pytest.raises(ValueError):
        parse_client_message("[]")


def test_shell_environment_strips_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.services.terminal import shell_environment

    monkeypatch.setenv("PIDECK_SESSION_SECRET", "super-secret-value")
    monkeypatch.setenv("PIDECK_DATABASE", "./data/pideck.db")
    env = shell_environment()
    assert "PIDECK_SESSION_SECRET" not in env
    assert "PIDECK_DATABASE" not in env
    assert env["TERM"] == "xterm-256color"
