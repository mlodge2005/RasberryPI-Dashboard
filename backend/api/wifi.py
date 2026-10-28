"""Authenticated upstream Wi-Fi endpoints. The hotspot adapter is not exposed."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from backend.auth.dependencies import get_current_user
from backend.database import UserRecord
from backend.models.schemas import WifiConnectRequest, WifiScanResponse, WifiStatus
from backend.services.wifi import SocketWifiBackend, WifiBackend
from backend.services.wifi_nm import WifiError

router = APIRouter()


def _backend(request: Request) -> WifiBackend:
    override = getattr(request.app.state, "wifi_backend", None)
    if override is not None:
        return override
    return SocketWifiBackend()


def _call(request: Request, op: str, body: dict[str, object] | None = None) -> dict[str, object]:
    try:
        return _backend(request).call(op, body or {})
    except WifiError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.get("/status", response_model=WifiStatus)
def wifi_status(
    request: Request,
    _: UserRecord = Depends(get_current_user),
) -> WifiStatus:
    result = _call(request, "status")
    return WifiStatus.model_validate(result["status"])


@router.post("/scan", response_model=WifiScanResponse)
def wifi_scan(
    request: Request,
    _: UserRecord = Depends(get_current_user),
) -> WifiScanResponse:
    return WifiScanResponse.model_validate(_call(request, "scan"))


@router.post("/connect", response_model=WifiStatus)
def wifi_connect(
    body: WifiConnectRequest,
    request: Request,
    _: UserRecord = Depends(get_current_user),
) -> WifiStatus:
    result = _call(
        request,
        "connect",
        {"ssid": body.ssid, "password": body.password, "saved": body.saved},
    )
    return WifiStatus.model_validate(result["status"])


@router.post("/disconnect", response_model=WifiStatus)
def wifi_disconnect(
    request: Request,
    _: UserRecord = Depends(get_current_user),
) -> WifiStatus:
    result = _call(request, "disconnect")
    return WifiStatus.model_validate(result["status"])
