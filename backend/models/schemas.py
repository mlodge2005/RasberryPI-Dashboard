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


WifiLinkState = Literal[
    "connected",
    "connecting",
    "disconnected",
    "disconnecting",
    "unavailable",
    "unmanaged",
    "failed",
    "deactivating",
    "prepare",
    "config",
    "ip-config",
    "ip-check",
    "secondaries",
    "need-auth",
    "unknown",
]
WifiConnectivity = Literal["full", "limited", "portal", "none", "unknown"]
WifiSecurity = Literal["open", "wpa-psk", "sae", "transition", "unsupported"]


class WifiStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    interface: Literal["wlan1"]
    state: WifiLinkState
    ssid: str = ""
    signal: int | None = Field(default=None, ge=0, le=100)
    ip_address: str | None = None
    gateway: str | None = None
    dns: list[str] = Field(default_factory=list)
    connectivity: WifiConnectivity
    connection_name: str = ""


class WifiNetwork(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ssid: str
    signal: int = Field(ge=0, le=100)
    security: WifiSecurity
    security_label: str
    saved: bool
    in_use: bool
    supported: bool


class WifiScanResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: WifiStatus
    networks: list[WifiNetwork]


class WifiConnectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ssid: str = Field(min_length=1, max_length=32)
    password: str = Field(default="", max_length=64)
    saved: bool = False


class DeviceRecord(BaseModel):
    mac: str
    alias: str
    hostname: str
    ip_address: str | None = None
    status: Literal["online", "offline", "unknown"]
    first_seen: str
    last_seen: str
    authorized: bool | None = None
    connected_seconds: int | None = None
    signal_dbm: int | None = None
    rx_bytes: int | None = None
    tx_bytes: int | None = None
    rx_packets: int | None = None
    tx_packets: int | None = None


class DeviceInventory(BaseModel):
    devices: list[DeviceRecord]
    connected: int
    known: int
    unidentified: int
    hostapd: Literal["ok", "unavailable"]
    dhcp: Literal["ok", "unavailable"]
    neighbors: Literal["ok", "unavailable"]


class DeviceAliasRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alias: str = Field(default="", max_length=40)

    @field_validator("alias")
    @classmethod
    def plain_alias(cls, value: str) -> str:
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("Alias cannot contain control characters")
        return " ".join(value.split())
