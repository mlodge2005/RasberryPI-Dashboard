"""FastAPI application factory."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from starlette.types import ASGIApp

from backend.api.router import api_router
from backend.api.terminal import router as terminal_router
from backend.auth.rate_limit import LoginRateLimiter
from backend.config import (
    REPO_ROOT,
    assert_process_not_binding_public,
    get_settings,
    resolve_session_secret,
)
from backend.database import init_db
from backend.services.system_monitor import SystemMonitor
from backend.services.terminal import TerminalSlots
from backend.utils.security import OriginCheckMiddleware, SecurityHeadersMiddleware

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    assert_process_not_binding_public()
    settings = get_settings()
    settings.validate_bind()
    secret = resolve_session_secret(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_db()
        monitor = SystemMonitor()
        app.state.monitor = monitor
        monitor.start()
        logger.info(
            "PiDeck ready (host %s, port %s, dev %s, database %s)",
            settings.pideck_host,
            settings.pideck_port,
            settings.pideck_dev,
            settings.database_path,
        )
        try:
            yield
        finally:
            monitor.stop()

    app = FastAPI(
        title="PiDeck",
        lifespan=lifespan,
        docs_url="/docs" if settings.pideck_dev else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.pideck_dev else None,
    )
    app.state.settings = settings
    app.state.session_secret = secret
    app.state.rate_limiter = LoginRateLimiter()
    app.state.shell_factory = None
    app.state.terminal_slots = TerminalSlots()

    app.add_middleware(OriginCheckMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(api_router, prefix="/api")
    app.include_router(terminal_router)
    _mount_frontend(app, REPO_ROOT / "frontend" / "dist")
    return app


def _mount_frontend(app: FastAPI, dist: Path) -> None:
    index = dist / "index.html"
    if not index.is_file():
        logger.warning("Frontend build not found at %s", dist)

        @app.get("/", include_in_schema=False)
        async def frontend_missing() -> JSONResponse:
            return JSONResponse(
                {
                    "detail": (
                        "Frontend is not built. In development, use the Vite server. "
                        "In production, run npm run build inside frontend/."
                    )
                },
                status_code=503,
            )

        return

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str) -> FileResponse:
        candidate = safe_static_file(dist, full_path)
        if candidate is not None:
            return FileResponse(candidate)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})


def safe_static_file(dist: Path, full_path: str) -> Path | None:
    if not full_path or full_path.startswith(("/", "\\")) or "\x00" in full_path:
        return None
    root = dist.resolve()
    candidate = (root / full_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    if candidate.is_file():
        return candidate
    return None


def __getattr__(name: str) -> ASGIApp:
    if name == "app":
        application = create_app()
        globals()["app"] = application
        return application
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
