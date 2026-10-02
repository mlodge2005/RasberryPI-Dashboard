"""Per-user theme settings and device-wide battery calibration."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from backend.auth.dependencies import get_current_user
from backend.auth.sessions import load_theme
from backend.database import UserRecord, save_theme_json
from backend.models.schemas import (
    BatterySettingsUpdate,
    BatterySettingsView,
    SettingsResponse,
    ThemeSettings,
)
from backend.services.battery_store import get_battery_settings, save_battery_settings

router = APIRouter()


@router.get("/settings", response_model=SettingsResponse)
def get_settings_route(user: UserRecord = Depends(get_current_user)) -> SettingsResponse:
    return SettingsResponse(theme=load_theme(user.id))


@router.put("/settings", response_model=SettingsResponse)
def put_settings_route(
    theme: ThemeSettings,
    user: UserRecord = Depends(get_current_user),
) -> SettingsResponse:
    save_theme_json(user.id, theme.model_dump_json())
    return SettingsResponse(theme=theme)


@router.get("/settings/battery", response_model=BatterySettingsView)
def get_battery_settings_route(_user: UserRecord = Depends(get_current_user)) -> BatterySettingsView:
    return _battery_view()


@router.put("/settings/battery", response_model=BatterySettingsView)
def put_battery_settings_route(
    body: BatterySettingsUpdate,
    _user: UserRecord = Depends(get_current_user),
) -> BatterySettingsView:
    runtime = _minutes(body.estimated_full_runtime_minutes, 15, 24 * 60, "Full runtime")
    charge = _minutes(body.full_charge_time_minutes, 1, 24 * 60, "Full charge time")
    save_battery_settings(runtime, charge)
    return _battery_view()


def _battery_view() -> BatterySettingsView:
    config, count, samples, median = get_battery_settings()
    return BatterySettingsView(
        estimated_full_runtime_minutes=config.estimated_full_runtime_minutes,
        full_charge_time_minutes=config.full_charge_time_minutes,
        calibration_median_minutes=None if median is None else round(median, 1),
        calibration_sample_count=count,
        calibration_samples_minutes=samples,
    )


def _minutes(value: float | None, low: float, high: float, label: str) -> float | None:
    if value is None:
        return None
    if value < low or value > high:
        raise HTTPException(status_code=400, detail=f"{label} must be between {low} and {high} minutes")
    return value
