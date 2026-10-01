from __future__ import annotations

from fastapi.testclient import TestClient

from backend.tests.constants import ORIGIN, PASSWORD

INCORRECT = "Incorrect username or password"


def _create_admin(password_hash: str) -> None:
    from backend.database import create_user

    create_user("admin", password_hash)


def test_health_is_public(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"


def test_login_success_sets_httponly_cookie(client: TestClient, password_hash: str) -> None:
    _create_admin(password_hash)
    response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": PASSWORD},
        headers=ORIGIN,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["username"] == "admin"
    assert body["theme"]["preset"] == "blue"
    assert "password" not in body
    cookie = response.headers["set-cookie"].lower()
    assert "pideck_session=" in cookie
    assert "httponly" in cookie
    assert "samesite=lax" in cookie
    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["username"] == "admin"


def test_login_cookie_is_secure_behind_https_proxy(client: TestClient, password_hash: str) -> None:
    _create_admin(password_hash)
    response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": PASSWORD},
        headers={**ORIGIN, "X-Forwarded-Proto": "https"},
    )
    assert response.status_code == 200
    assert "secure" in response.headers["set-cookie"].lower()


def test_invalid_login_is_generic(client: TestClient, password_hash: str) -> None:
    _create_admin(password_hash)
    missing = client.post(
        "/api/auth/login",
        json={"username": "ghost", "password": "not-the-password"},
        headers=ORIGIN,
    )
    wrong = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "not-the-password"},
        headers=ORIGIN,
    )
    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert missing.json()["detail"] == INCORRECT
    assert wrong.json()["detail"] == INCORRECT
    assert "set-cookie" not in missing.headers


def test_password_is_hashed(password_hash: str) -> None:
    assert password_hash != PASSWORD
    assert password_hash.startswith("$argon2")


def test_protected_routes_require_auth(client: TestClient) -> None:
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/system/snapshot").status_code == 401
    assert client.get("/api/system/stats").status_code == 401
    assert client.get("/api/system/info").status_code == 401
    assert client.get("/api/settings").status_code == 401


def test_logout_invalidates_session(auth_client: TestClient) -> None:
    assert auth_client.get("/api/auth/me").status_code == 200
    logged_out = auth_client.post("/api/auth/logout", headers=ORIGIN)
    assert logged_out.status_code == 200
    assert auth_client.get("/api/auth/me").status_code == 401


def test_login_replaces_session(auth_client: TestClient) -> None:
    first = auth_client.cookies.get("pideck_session")
    again = auth_client.post(
        "/api/auth/login",
        json={"username": "admin", "password": PASSWORD},
        headers=ORIGIN,
    )
    assert again.status_code == 200
    second = auth_client.cookies.get("pideck_session")
    assert first and second and first != second


def test_cross_origin_login_is_blocked(client: TestClient, password_hash: str) -> None:
    _create_admin(password_hash)
    response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": PASSWORD},
        headers={"Origin": "https://evil.example"},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "Cross-origin request blocked"


def test_login_rate_limit(client: TestClient) -> None:
    from backend.auth.rate_limit import LoginRateLimiter

    client.app.state.rate_limiter = LoginRateLimiter(max_attempts=2, window_seconds=600)
    payload = {"username": "nobody", "password": "wrong-password-value"}
    first = client.post("/api/auth/login", json=payload, headers=ORIGIN)
    second = client.post("/api/auth/login", json=payload, headers=ORIGIN)
    third = client.post("/api/auth/login", json=payload, headers=ORIGIN)
    assert first.status_code == 401
    assert second.status_code == 401
    assert third.status_code == 429
    assert third.json()["detail"] == "Too many login attempts. Try again later."


def test_settings_roundtrip_and_validation(auth_client: TestClient) -> None:
    theme = {
        "preset": "hacker",
        "primary": "#16a34a",
        "secondary": "#14532d",
        "accent": "#4ade80",
        "background": "#070a07",
        "panel": "#101610",
        "text": "#d7f5df",
    }
    saved = auth_client.put("/api/settings", json=theme, headers=ORIGIN)
    assert saved.status_code == 200
    assert saved.json()["theme"]["preset"] == "hacker"
    assert saved.json()["theme"]["primary"] == "#16a34a"
    loaded = auth_client.get("/api/settings")
    assert loaded.json()["theme"] == saved.json()["theme"]
    me = auth_client.get("/api/auth/me")
    assert me.json()["theme"]["accent"] == "#4ade80"

    rejected = auth_client.put(
        "/api/settings",
        json={**theme, "primary": "red; background: url(https://evil)"},
        headers=ORIGIN,
    )
    assert rejected.status_code == 422


def test_init_db_does_not_remove_users(auth_client: TestClient) -> None:
    from backend.database import init_db

    init_db()
    assert auth_client.get("/api/auth/me").status_code == 200
