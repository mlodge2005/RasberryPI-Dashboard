"""Battery readings from psutil and Linux power_supply sysfs.

Missing hardware returns an explicit unavailable result. It does not raise.
"""

from __future__ import annotations

import logging
from pathlib import Path

import psutil

from backend.models.schemas import BatteryInfo

logger = logging.getLogger(__name__)

UNAVAILABLE = "Battery information unavailable"


def read_battery() -> BatteryInfo:
    try:
        sysfs = _read_sysfs()
        if sysfs is not None:
            return sysfs
        sensed = _read_psutil()
        if sensed is not None:
            return sensed
    except Exception:
        logger.warning("Battery probe failed", exc_info=True)
    return BatteryInfo(available=False, message=UNAVAILABLE)


def _read_psutil() -> BatteryInfo | None:
    try:
        battery = psutil.sensors_battery()
    except Exception:
        return None
    if battery is None:
        return None
    status = "charging" if battery.power_plugged else "discharging"
    if battery.power_plugged and battery.percent is not None and battery.percent >= 99:
        status = "full"
    return BatteryInfo(
        available=True,
        percent=_clamp_percent(battery.percent),
        charging=bool(battery.power_plugged) and status != "full",
        status=status,
        voltage=None,
        message=None,
    )


def _read_sysfs() -> BatteryInfo | None:
    root = Path("/sys/class/power_supply")
    if not root.is_dir():
        return None
    for entry in sorted(root.iterdir()):
        kind = _read_text(entry / "type")
        if kind is None or kind.lower() != "battery":
            continue
        percent = _capacity(entry)
        status_raw = _read_text(entry / "status")
        voltage = _voltage(entry)
        if percent is None and status_raw is None and voltage is None:
            continue
        status, charging = _status(status_raw)
        return BatteryInfo(
            available=True,
            percent=percent,
            charging=charging,
            status=status,
            voltage=voltage,
            message=None,
        )
    return None


def _capacity(entry: Path) -> float | None:
    direct = _read_float(entry / "capacity")
    if direct is not None:
        return _clamp_percent(direct)
    for now_name, full_name in (
        ("energy_now", "energy_full"),
        ("charge_now", "charge_full"),
    ):
        now = _read_float(entry / now_name)
        full = _read_float(entry / full_name)
        if now is not None and full:
            return _clamp_percent(now / full * 100)
    return None


def _status(raw: str | None) -> tuple[str | None, bool | None]:
    if not raw:
        return None, None
    status = raw.strip().lower()
    if status == "charging":
        return status, True
    if status in {"discharging", "not charging", "full"}:
        return status, False
    return status, None


def _voltage(entry: Path) -> float | None:
    microvolts = _read_float(entry / "voltage_now")
    if microvolts is None:
        microvolts = _read_float(entry / "voltage_avg")
    if microvolts is None:
        return None
    # Linux documents these files as microvolts. Some hats write millivolts.
    if microvolts > 1000:
        volts = microvolts / 1_000_000
        if volts > 100:
            volts = microvolts / 1000
    else:
        volts = microvolts
    return round(volts, 3)


def _clamp_percent(value: float | None) -> float | None:
    if value is None:
        return None
    return round(max(0.0, min(100.0, float(value))), 1)


def _read_text(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    return text or None


def _read_float(path: Path) -> float | None:
    text = _read_text(path)
    if text is None:
        return None
    try:
        return float(text)
    except ValueError:
        return None
