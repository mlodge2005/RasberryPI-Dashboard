"""Track PiSugar S Plus power sessions and build the dashboard estimate.

CPU load is intentionally not part of this estimate. Time on battery, compared
with a calibrated full runtime, is enough for the first version.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

import psutil

from backend.database import connect
from backend.models.schemas import BatteryEstimate, BatteryUsageStats
from backend.services.battery_math import (
    charge_is_complete,
    charge_recommended_seconds,
    estimated_percent,
    level_for,
    remaining_seconds,
    sample_duration_ok,
)
from backend.services.battery_store import (
    BatteryConfig,
    StoredSession,
    add_sample,
    close_session,
    ensure_config,
    get_open,
    insert_session,
    load_config,
    sessions_since,
    calibration_summary,
    stats_window_start,
    touch,
)
from backend.services.pisugar_gpio import PowerReading, read_external_power

DISCLAIMER = (
    "Estimated charge is a time-based estimate, not a battery percentage from the PiSugar."
)
ASSUMED_DETAIL = (
    "Tracking started from an assumed full charge. This is not a measured charge level."
)
HEARTBEAT = timedelta(seconds=30)
REBOOT_SKEW = timedelta(seconds=20)

_lock = threading.Lock()


def current_estimate() -> BatteryEstimate:
    return observe_power(read_external_power())


def observe_power(
    reading: PowerReading,
    *,
    now: datetime | None = None,
    boot_time: datetime | None = None,
) -> BatteryEstimate:
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    booted = boot_time or datetime.fromtimestamp(psutil.boot_time(), timezone.utc)
    if booted.tzinfo is None:
        booted = booted.replace(tzinfo=timezone.utc)
    with _lock:
        with connect() as connection:
            return _observe(connection, reading, moment, booted)


def _observe(connection, reading: PowerReading, now: datetime, boot_time: datetime) -> BatteryEstimate:
    ensure_config(connection, now)
    config = load_config(connection)
    session = get_open(connection)
    frozen = reading.external_power is None
    if session is not None and not frozen:
        session = _reconcile_reboot(connection, session, reading, config, now, boot_time)
        config = load_config(connection)
    if frozen:
        return _build(connection, config, session, reading, now, frozen=True)

    source = "external" if reading.external_power else "battery"
    if session is None:
        session = _begin(connection, source, now, None, source == "battery")
    elif session.power_source != source:
        session = _switch(connection, session, config, source, now)
        config = load_config(connection)
    elif now - session.last_seen_at >= HEARTBEAT:
        touch(connection, session.id, now)
        session = get_open(connection)
    return _build(connection, config, session, reading, now, frozen=False)


def _begin(
    connection,
    source: str,
    now: datetime,
    preserved: tuple[float | None, bool, str] | None,
    assume_full: bool,
) -> StoredSession:
    if preserved is not None:
        percent, uncertain, reason = preserved
        return insert_session(
            connection,
            started_at=now,
            power_source=source,
            start_percent=percent,
            uncertain=uncertain,
            start_reason=reason,
        )
    if source == "battery" and assume_full:
        return insert_session(
            connection,
            started_at=now,
            power_source="battery",
            start_percent=100.0,
            uncertain=False,
            start_reason="assumed",
        )
    return insert_session(
        connection,
        started_at=now,
        power_source=source,
        start_percent=None,
        uncertain=True,
        start_reason="preserved",
    )


def _switch(
    connection,
    session: StoredSession,
    config: BatteryConfig,
    source: str,
    now: datetime,
) -> StoredSession:
    if session.power_source == "battery":
        end_percent = _percent_at(session, config, now)
        close_session(connection, session, now, end_percent)
        return insert_session(
            connection,
            started_at=now,
            power_source="external",
            start_percent=end_percent,
            uncertain=True,
            start_reason="preserved",
        )
    return _open_battery_after_charge(connection, session, config, now, now)


def _open_battery_after_charge(
    connection,
    charge: StoredSession,
    config: BatteryConfig,
    end_time: datetime,
    start_time: datetime,
) -> StoredSession:
    complete = charge_is_complete(charge.started_at, end_time, config.full_charge_time_minutes)
    close_session(connection, charge, end_time, 100.0 if complete else charge.estimated_start_percent)
    if complete:
        return insert_session(
            connection,
            started_at=start_time,
            power_source="battery",
            start_percent=100.0,
            uncertain=False,
            start_reason="full_charge",
        )
    return insert_session(
        connection,
        started_at=start_time,
        power_source="battery",
        start_percent=charge.estimated_start_percent,
        uncertain=True,
        start_reason="preserved",
    )


def _reconcile_reboot(
    connection,
    session: StoredSession,
    reading: PowerReading,
    config: BatteryConfig,
    now: datetime,
    boot_time: datetime,
) -> StoredSession | None:
    if not _booted_after(session, boot_time, now):
        return session
    if session.power_source == "external" and reading.external_power is True:
        return session
    if session.power_source == "battery":
        return _finish_battery_after_reboot(connection, session, reading, config, now)
    if session.power_source == "external" and reading.external_power is False:
        return _open_battery_after_charge(connection, session, config, session.last_seen_at, now)
    return session


def _finish_battery_after_reboot(
    connection,
    session: StoredSession,
    reading: PowerReading,
    config: BatteryConfig,
    now: datetime,
) -> StoredSession:
    end_percent = _percent_at(session, config, session.last_seen_at)
    duration = close_session(connection, session, session.last_seen_at, end_percent)
    eligible = _eligible_full_discharge(session, duration, reading.external_power is True)
    if eligible:
        add_sample(connection, now, duration, session.id, config)
    if reading.external_power is True:
        start_percent = 0.0 if eligible else end_percent
        return insert_session(
            connection,
            started_at=now,
            power_source="external",
            start_percent=start_percent,
            uncertain=True,
            start_reason="depleted" if eligible else "preserved",
        )
    preserved_percent = session.estimated_start_percent if end_percent is None else end_percent
    return insert_session(
        connection,
        started_at=now,
        power_source="battery",
        start_percent=preserved_percent,
        uncertain=session.uncertain,
        start_reason="preserved",
    )


def _eligible_full_discharge(session: StoredSession, duration: float, external_now: bool) -> bool:
    if not external_now or session.uncertain or session.power_source != "battery":
        return False
    if session.start_reason != "full_charge":
        return False
    if session.estimated_start_percent is None or session.estimated_start_percent < 99:
        return False
    return sample_duration_ok(duration)


def _booted_after(session: StoredSession, boot_time: datetime, now: datetime) -> bool:
    if boot_time > now + timedelta(seconds=5):
        return False
    return boot_time > session.last_seen_at + REBOOT_SKEW


def _percent_at(session: StoredSession, config: BatteryConfig, at: datetime) -> float | None:
    elapsed = (at - session.started_at).total_seconds()
    return estimated_percent(session.estimated_start_percent, elapsed, config.estimated_full_runtime_minutes)


def _build(
    connection,
    config: BatteryConfig,
    session: StoredSession | None,
    reading: PowerReading,
    now: datetime,
    *,
    frozen: bool,
) -> BatteryEstimate:
    count, samples, _median = calibration_summary(connection)
    stats = _stats(connection, config, session, now, frozen, count, samples)
    if session is None:
        return BatteryEstimate(
            external_power=None,
            power_label="Power source unknown",
            level="unknown",
            detail=reading.detail,
            tracking=False,
            disclaimer=DISCLAIMER,
            stats=stats,
        )

    on_battery = session.power_source == "battery"
    mark = session.last_seen_at if frozen else now
    if on_battery:
        percent = _percent_at(session, config, mark)
        uncertain = session.uncertain or frozen
        full = False
    else:
        full = charge_is_complete(session.started_at, mark, config.full_charge_time_minutes)
        percent = 100.0 if full else None
        uncertain = not full or frozen

    remaining = None
    recommended = None
    if on_battery and percent is not None and not uncertain:
        remaining = remaining_seconds(config.estimated_full_runtime_minutes, percent)
        recommended = charge_recommended_seconds(config.estimated_full_runtime_minutes, percent)

    if frozen:
        level = "unknown"
        advice = "Power source unknown"
        power_label = "Power source unknown"
    elif not on_battery:
        level = "charging"
        power_label = "External power connected" if full else "Charging"
        advice = None if full else "Charge estimate uncertain"
        if full:
            advice = "Full charge assumed"
    elif uncertain:
        level = level_for(percent) if percent is not None else "unknown"
        advice = "Charge estimate uncertain"
        power_label = "Running on Battery"
    elif percent is None:
        level = "unknown"
        advice = "Set a full runtime in Settings to estimate charge"
        power_label = "Running on Battery"
    else:
        level = level_for(percent)
        advice = _advice_for(level)
        power_label = "Running on Battery"

    detail = reading.detail
    if detail is None and on_battery and session.start_reason == "assumed" and not frozen:
        detail = ASSUMED_DETAIL

    time_on_battery = (mark - session.started_at).total_seconds() if on_battery else None
    shown_percent = None if (not on_battery and not full) or (on_battery and percent is None) else percent
    if on_battery and uncertain and percent is None:
        shown_percent = None
    return BatteryEstimate(
        external_power=None if frozen else reading.external_power,
        power_label=power_label,
        estimated_percent=None if shown_percent is None else round(shown_percent, 1),
        estimate_uncertain=uncertain,
        time_on_battery_seconds=None if time_on_battery is None else max(0.0, time_on_battery),
        estimated_remaining_seconds=remaining,
        charge_recommended_in_seconds=recommended,
        level=level,  # type: ignore[arg-type]
        advice=advice,
        detail=detail,
        tracking=True,
        disclaimer=DISCLAIMER,
        stats=stats,
    )


def _advice_for(level: str) -> str | None:
    if level == "critical":
        return "Critical"
    if level == "charge_soon":
        return "Charge soon"
    if level == "low":
        return "Low"
    return None


def _stats(
    connection,
    config: BatteryConfig,
    session: StoredSession | None,
    now: datetime,
    frozen: bool,
    count: int,
    samples: list[float],
) -> BatteryUsageStats:
    local_now = now.astimezone()
    today_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = local_now - timedelta(days=7)
    rows = sessions_since(connection, stats_window_start(now))
    today = 0.0
    week = 0.0
    durations: list[float] = []
    for row in rows:
        end = _row_end(row, session, now, frozen)
        today += _overlap(row.started_at, end, today_start, local_now)
        week += _overlap(row.started_at, end, week_start, local_now)
        if (
            row.ended_at is not None
            and row.ended_at >= week_start
            and row.duration_seconds
            and row.duration_seconds > 0
        ):
            durations.append(float(row.duration_seconds))
    current = None
    if session is not None and session.power_source == "battery":
        mark = session.last_seen_at if frozen else now
        current = max(0.0, (mark - session.started_at).total_seconds())
    average = sum(durations) / len(durations) if durations else None
    return BatteryUsageStats(
        current_session_seconds=current,
        today_seconds=today,
        week_seconds=week,
        week_average_session_seconds=average,
        week_session_count=len(durations),
        estimated_full_runtime_minutes=config.estimated_full_runtime_minutes,
        calibration_sample_count=count,
        calibration_samples_minutes=samples,
    )


def _row_end(row: StoredSession, open_session: StoredSession | None, now: datetime, frozen: bool) -> datetime:
    if row.ended_at is not None:
        return row.ended_at
    if open_session is not None and row.id == open_session.id:
        return open_session.last_seen_at if frozen else now
    return row.last_seen_at


def _overlap(start: datetime, end: datetime, window_start: datetime, window_end: datetime) -> float:
    left = max(start, window_start)
    right = min(end, window_end)
    return max(0.0, (right - left).total_seconds())
