"""HTTP security helpers: cookies, origin checks, and response headers."""

from __future__ import annotations

from urllib.parse import urlsplit

from starlette.datastructures import Headers, MutableHeaders
from starlette.requests import HTTPConnection
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from backend.config import Settings, get_settings

# Scripts stay same-origin. Style attributes are allowed so the theme preview
# can set CSS variables. The session cookie is HttpOnly, so script injection
# still cannot read it, and script-src does not allow inline JavaScript.
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "style-src-attr 'unsafe-inline'; "
    "img-src 'self' data:; "
    "font-src 'self'; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)


def cookie_is_secure(connection: HTTPConnection, settings: Settings) -> bool:
    mode = settings.pideck_cookie_secure
    if mode in {"true", "1", "yes"}:
        return True
    if mode in {"false", "0", "no"}:
        return False
    forwarded = connection.headers.get("x-forwarded-proto", "")
    if forwarded.split(",")[0].strip().lower() == "https":
        return True
    return connection.url.scheme == "https"


def client_key(headers: Headers, client_host: str | None) -> str:
    """Prefer the Tailscale client address. Safe because the app binds to localhost."""
    forwarded = headers.get("x-forwarded-for")
    if forwarded:
        candidate = forwarded.split(",")[0].strip()
        if candidate:
            return candidate[:128]
    return (client_host or "unknown")[:128]


def origin_allowed(headers: Headers) -> bool:
    origin = headers.get("origin")
    host = headers.get("host")
    if not origin or not host:
        return False
    parsed = urlsplit(origin)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False
    if parsed.username or parsed.password:
        return False
    if parsed.netloc.lower() == host.lower():
        return True
    settings = get_settings()
    if settings.pideck_dev and parsed.hostname in {"localhost", "127.0.0.1"}:
        if parsed.port in {5173, 4173, 8080}:
            return True
    return False


class OriginCheckMiddleware:
    """Reject cross-site mutating API calls. WebSocket origin is checked on the socket."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"] not in {"GET", "HEAD", "OPTIONS"}:
            path = scope.get("path", "")
            if path.startswith("/api"):
                headers = Headers(scope=scope)
                if not origin_allowed(headers):
                    response = JSONResponse({"detail": "Cross-origin request blocked"}, status_code=403)
                    await response(scope, receive, send)
                    return
        await self.app(scope, receive, send)


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["X-Content-Type-Options"] = "nosniff"
                headers["X-Frame-Options"] = "DENY"
                headers["Referrer-Policy"] = "same-origin"
                headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
                headers["X-Permitted-Cross-Domain-Policies"] = "none"
                if not get_settings().pideck_dev:
                    headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
                path = scope.get("path", "")
                if path.startswith("/api"):
                    headers["Cache-Control"] = "no-store"
            await send(message)

        await self.app(scope, receive, send_with_headers)
