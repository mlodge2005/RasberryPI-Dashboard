"""API routers."""

from __future__ import annotations

from fastapi import APIRouter

from backend.api import auth, devices, settings, system, wifi

api_router = APIRouter()
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(system.router, prefix="/system", tags=["system"])
api_router.include_router(settings.router, tags=["settings"])
api_router.include_router(wifi.router, prefix="/wifi", tags=["wifi"])
api_router.include_router(devices.router, prefix="/devices", tags=["devices"])


@api_router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
