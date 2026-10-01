"""System snapshot endpoints. All of them require a session."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from backend.auth.dependencies import get_current_user
from backend.database import UserRecord
from backend.models.schemas import SystemInfo, SystemSnapshot, SystemStats
from backend.services.system_monitor import SystemMonitor

router = APIRouter()


def _monitor(request: Request) -> SystemMonitor:
    return request.app.state.monitor


@router.get("/snapshot", response_model=SystemSnapshot)
def snapshot(
    request: Request,
    _: UserRecord = Depends(get_current_user),
) -> SystemSnapshot:
    return _monitor(request).snapshot()


@router.get("/stats", response_model=SystemStats)
def stats(
    request: Request,
    _: UserRecord = Depends(get_current_user),
) -> SystemStats:
    current = _monitor(request).snapshot()
    return SystemStats(
        cpu=current.cpu,
        memory=current.memory,
        storage=current.storage,
        network=current.network,
        battery=current.battery,
    )


@router.get("/info", response_model=SystemInfo)
def info(
    request: Request,
    _: UserRecord = Depends(get_current_user),
) -> SystemInfo:
    return _monitor(request).snapshot().system
