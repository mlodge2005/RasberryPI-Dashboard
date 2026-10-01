from __future__ import annotations

from pathlib import Path

import pytest


def test_refuses_public_bind(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PIDECK_HOST", "0.0.0.0")
    monkeypatch.setenv("PIDECK_ALLOW_NON_LOCALHOST", "false")
    from backend.config import get_settings

    get_settings.cache_clear()
    with pytest.raises(SystemExit):
        get_settings().validate_bind()


def test_localhost_bind_is_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PIDECK_HOST", "127.0.0.1")
    monkeypatch.setenv("PIDECK_ALLOW_NON_LOCALHOST", "false")
    from backend.config import get_settings

    get_settings.cache_clear()
    get_settings().validate_bind()


def test_weak_secret_refused_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PIDECK_SESSION_SECRET", "changeme")
    monkeypatch.setenv("PIDECK_DEV", "false")
    from backend.config import get_settings, resolve_session_secret

    get_settings.cache_clear()
    with pytest.raises(SystemExit):
        resolve_session_secret(get_settings())


def test_dev_placeholder_secret_is_ephemeral(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PIDECK_SESSION_SECRET", "replace-with-a-long-random-string")
    monkeypatch.setenv("PIDECK_DEV", "true")
    from backend.config import get_settings, resolve_session_secret

    get_settings.cache_clear()
    secret = resolve_session_secret(get_settings())
    assert len(secret) >= 32
    assert secret != "replace-with-a-long-random-string"


def test_static_path_traversal(tmp_path: Path) -> None:
    from backend.app import safe_static_file

    dist = tmp_path / "dist"
    dist.mkdir()
    asset = dist / "app.js"
    asset.write_text("console.log(1)\n", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("nope", encoding="utf-8")
    assert safe_static_file(dist, "app.js") == asset.resolve()
    assert safe_static_file(dist, "../secret.txt") is None
    assert safe_static_file(dist, "..\\secret.txt") is None
    assert safe_static_file(dist, "") is None
    assert safe_static_file(dist, "/etc/passwd") is None
