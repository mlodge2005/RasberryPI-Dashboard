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


BatteryLevel = Literal["normal", "low", "charge_soon", "critical", "charging", "unknown"]


class BatteryUsageStats(BaseModel):
    current_session_seconds: float | None = None
    today_seconds: float = 0
    week_seconds: float = 0
    week_average_session_seconds: float | None = None
    week_session_count: int = 0
    estimated_full_runtime_minutes: float | None = None
    calibration_sample_count: int = 0
    calibration_samples_minutes: list[float] = Field(default_factory=list)


class BatteryEstimate(BaseModel):
    """Time-based PiSugar S Plus estimate. This is not a fuel-gauge reading."""

    model: str = "PiSugar S Plus"
    external_power: bool | None = None
    power_label: str
    estimated_percent: float | None = None
    estimate_uncertain: bool = False
    time_on_battery_seconds: float | None = None
    estimated_remaining_seconds: float | None = None
    charge_recommended_in_seconds: float | None = None
    level: BatteryLevel = "unknown"
    advice: str | None = None
    detail: str | None = None
    tracking: bool = False
    disclaimer: str
    stats: BatteryUsageStats


class BatterySettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    estimated_full_runtime_minutes: float | None = None
    full_charge_time_minutes: float | None = None


class BatterySettingsView(BaseModel):
    estimated_full_runtime_minutes: float | None = None
    full_charge_time_minutes: float | None = None
    calibration_median_minutes: float | None = None
    calibration_sample_count: int = 0
    calibration_samples_minutes: list[float] = Field(default_factory=list)


class BatteryInfo(BaseModel):
    available: bool
    percent: float | None = None
    charging: bool | None = None
    status: str | None = None
    voltage: float | None = None
    message: str | None = None
    estimate: BatteryEstimate | None = None


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
