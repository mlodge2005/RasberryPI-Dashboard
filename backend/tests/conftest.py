from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.tests.constants import ORIGIN, PASSWORD


@pytest.fixture(autouse=True)
def _isolated_settings():
    from backend.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("PIDECK_DATABASE", str(tmp_path / "pideck.db"))
    monkeypatch.setenv("PIDECK_SESSION_SECRET", "test-session-secret-should-be-long-enough!!")
    monkeypatch.setenv("PIDECK_DEV", "true")
    monkeypatch.setenv("PIDECK_HOST", "127.0.0.1")
    monkeypatch.setenv("PIDECK_COOKIE_SECURE", "auto")
    monkeypatch.setenv("PIDECK_ALLOW_NON_LOCALHOST", "false")
    from backend.config import get_settings

    get_settings.cache_clear()
    from backend.app import create_app

    return create_app()


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def password_hash() -> str:
    from backend.auth.passwords import hash_password

    return hash_password(PASSWORD)


@pytest.fixture
def auth_client(client: TestClient, password_hash: str) -> TestClient:
    from backend.database import create_user

    create_user("admin", password_hash)
    response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": PASSWORD},
        headers=ORIGIN,
    )
    assert response.status_code == 200, response.text
    return client
