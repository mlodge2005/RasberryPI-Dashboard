"""Per-user theme settings."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from backend.auth.dependencies import get_current_user
from backend.auth.sessions import load_theme
from backend.database import UserRecord, save_theme_json
from backend.models.schemas import SettingsResponse, ThemeSettings

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
