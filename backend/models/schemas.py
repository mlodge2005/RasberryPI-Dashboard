"""API request and response models."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ThemePreset = Literal["blue", "hacker", "purple", "red", "custom"]
TailscaleStatus = Literal["online", "offline", "unavailable"]
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class ThemeSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preset: ThemePreset
    primary: str
    secondary: str
    accent: str
    background: str
    panel: str
    text: str

    @field_validator("primary", "secondary", "accent", "background", "panel", "text")
    @classmethod
    def hex_color(cls, value: str) -> str:
        if not isinstance(value, str) or _HEX.fullmatch(value) is None:
            raise ValueError("Color must be a #RRGGBB hex value")
        return value.lower()


def default_theme() -> ThemeSettings:
    return ThemeSettings(
        preset="blue",
        primary="#3b82f6",
        secondary="#1d4ed8",
        accent="#22d3ee",
        background="#0b1220",
        panel="#121a2b",
        text="#e7eefc",
    )


class UserProfile(BaseModel):
    id: int
    username: str
    theme: ThemeSettings


class SettingsResponse(BaseModel):
    theme: ThemeSettings


class CpuStats(BaseModel):
    usage_percent: float
    per_core_percent: list[float]
    temperature_c: float | None
    frequency_mhz: float | None
    load_average: list[float] | None


class MemoryStats(BaseModel):
    used_bytes: int
    total_bytes: int
    percent: float


class StorageStats(BaseModel):
    used_bytes: int
    free_bytes: int
    total_bytes: int
    percent: float
    mount: str


class NetworkStats(BaseModel):
    hostname: str
    local_ip: str | None
    tailscale_ip: str | None
    tailscale_hostname: str | None
    tailscale_status: TailscaleStatus
    bytes_sent: int
    bytes_received: int


class SystemInfo(BaseModel):
    os_name: str
    kernel: str
    architecture: str
    uptime_seconds: float
    boot_time: str
    pi_model: str | None


class BatteryInfo(BaseModel):
    available: bool
    percent: float | None = None
    charging: bool | None = None
    status: str | None = None
    voltage: float | None = None
    message: str | None = None


class SystemSnapshot(BaseModel):
    cpu: CpuStats
    memory: MemoryStats
    storage: StorageStats
    network: NetworkStats
    system: SystemInfo
    battery: BatteryInfo
    collected_at: str


class SystemStats(BaseModel):
    cpu: CpuStats
    memory: MemoryStats
    storage: StorageStats
    network: NetworkStats
    battery: BatteryInfo
