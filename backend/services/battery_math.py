"""Pure helpers for the PiSugar S Plus time-based charge estimate.

Nothing here reads hardware. The result is an estimate, not a fuel-gauge percentage.
"""

from __future__ import annotations

from datetime import datetime

MIN_SAMPLE_SECONDS = 15 * 60
MAX_SAMPLE_SECONDS = 24 * 60 * 60
MEDIAN_WINDOW = 5
MAX_STEP = 0.25


def estimated_percent(
    start_percent: float | None,
    elapsed_seconds: float,
    full_runtime_minutes: float | None,
) -> float | None:
    if start_percent is None or full_runtime_minutes is None or full_runtime_minutes <= 0:
        return None
    elapsed = max(0.0, elapsed_seconds)
    value = start_percent - (elapsed / (full_runtime_minutes * 60)) * 100
    return min(100.0, max(0.0, value))


def remaining_seconds(full_runtime_minutes: float | None, percent: float | None) -> float | None:
    if full_runtime_minutes is None or percent is None:
        return None
    return full_runtime_minutes * 60 * percent / 100


def charge_recommended_seconds(full_runtime_minutes: float | None, percent: float | None) -> float | None:
    """Seconds until the estimate reaches the 20% charge-soon line."""

    if full_runtime_minutes is None or percent is None or percent <= 20:
        return None
    return full_runtime_minutes * 60 * (percent - 20) / 100


def level_for(percent: float | None) -> str:
    if percent is None:
        return "unknown"
    if percent > 40:
        return "normal"
    if percent >= 20:
        return "low"
    if percent >= 10:
        return "charge_soon"
    return "critical"


def rolling_median(values: list[float]) -> float | None:
    window = values[-MEDIAN_WINDOW:]
    if not window:
        return None
    ordered = sorted(window)
    count = len(ordered)
    mid = count // 2
    if count % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def bound_runtime(current: float | None, proposed: float) -> float:
    """Keep one new median from moving the saved runtime by more than 25%."""

    if current is None or current <= 0:
        return proposed
    return min(current * (1 + MAX_STEP), max(current * (1 - MAX_STEP), proposed))


def sample_duration_ok(duration_seconds: float) -> bool:
    return MIN_SAMPLE_SECONDS <= duration_seconds <= MAX_SAMPLE_SECONDS


def charge_is_complete(started_at: datetime, at: datetime, full_charge_minutes: float | None) -> bool:
    if full_charge_minutes is None or full_charge_minutes <= 0:
        return False
    return (at - started_at).total_seconds() >= full_charge_minutes * 60
