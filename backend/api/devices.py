"""Authenticated hotspot device inventory. Discovery is read-only."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from backend.auth.dependencies import get_current_user
from backend.database import UserRecord
from backend.models.schemas import DeviceAliasRequest, DeviceInventory
from backend.services.device_store import set_alias
from backend.services.discovery import DeviceSources, refresh
from backend.services.discovery_parse import normalize_mac

router = APIRouter()


def _sources(request: Request) -> DeviceSources | None:
    override = getattr(request.app.state, "device_sources", None)
    if override is not None:
        return override
    return None


@router.get("", response_model=DeviceInventory)
def device_inventory(
    request: Request,
    _: UserRecord = Depends(get_current_user),
) -> DeviceInventory:
    return refresh(_sources(request))


@router.post("/refresh", response_model=DeviceInventory)
def device_refresh(
    request: Request,
    _: UserRecord = Depends(get_current_user),
) -> DeviceInventory:
    return refresh(_sources(request))


@router.put("/{mac}", response_model=DeviceInventory)
def device_alias(
    mac: str,
    body: DeviceAliasRequest,
    request: Request,
    _: UserRecord = Depends(get_current_user),
) -> DeviceInventory:
    normalized = normalize_mac(mac)
    if normalized is None:
        raise HTTPException(status_code=400, detail="That MAC address is not valid.")
    if not set_alias(normalized, body.alias):
        raise HTTPException(status_code=404, detail="That device has not been seen.")
    return refresh(_sources(request))
