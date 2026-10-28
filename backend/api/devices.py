"""Authenticated hotspot device inventory and one-client disconnect."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from backend.auth.dependencies import get_current_user
from backend.database import UserRecord
from backend.models.schemas import DeviceAliasRequest, DeviceDisconnectResponse, DeviceInventory
from backend.services.deauth import DeauthClient, DeauthError, SocketDeauth
from backend.services.device_store import set_alias
from backend.services.discovery import DeviceSources, refresh
from backend.services.discovery_parse import normalize_mac, unicast_mac

router = APIRouter()


def _sources(request: Request) -> DeviceSources | None:
    override = getattr(request.app.state, "device_sources", None)
    if override is not None:
        return override
    return None


def _deauth(request: Request) -> DeauthClient:
    override = getattr(request.app.state, "deauth_client", None)
    if override is not None:
        return override
    return SocketDeauth()


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


@router.post("/{mac}/disconnect", response_model=DeviceDisconnectResponse)
def device_disconnect(
    mac: str,
    request: Request,
    _: UserRecord = Depends(get_current_user),
) -> DeviceDisconnectResponse:
    normalized = unicast_mac(mac)
    if normalized is None:
        raise HTTPException(status_code=400, detail="That MAC address cannot be disconnected.")
    current = refresh(_sources(request))
    if current.hostapd != "ok":
        detail = current.hostapd_error or "Association data is unavailable."
        raise HTTPException(status_code=503, detail=detail)
    match = next((item for item in current.devices if item.mac == normalized), None)
    if match is None or match.status != "online":
        raise HTTPException(status_code=409, detail="That device is not currently associated.")
    try:
        _deauth(request).disconnect(normalized)
    except DeauthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return DeviceDisconnectResponse(
        detail="Disconnect requested. The device can reconnect to the hotspot on its own.",
        inventory=refresh(_sources(request)),
    )
