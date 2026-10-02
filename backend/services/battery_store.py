"""SQLite persistence for PiSugar runtime sessions and calibration.

Rows are added. Nothing here drops tables or rewrites unrelated data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from backend.database import connect, utc_now
from backend.services.battery_math import bound_runtime, rolling_median, sample_duration_ok


@dataclass(frozen=True)
class BatteryConfig:
    estimated_full_runtime_minutes: float | None
    full_charge_time_minutes: float | None


@dataclass(frozen=True)
class StoredSession:
    id: int
    started_at: datetime
    ended_at: datetime | None
    last_seen_at: datetime
    duration_seconds: int | None
    estimated_start_percent: float | None
    estimated_end_percent: float | None
    power_source: str
    uncertain: bool
    start_reason: str


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _parse(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _session(row) -> StoredSession:
    return StoredSession(
        id=int(row["id"]),
        started_at=_parse(str(row["started_at"])) or utc_now(),
        ended_at=_parse(row["ended_at"]),
        last_seen_at=_parse(str(row["last_seen_at"])) or utc_now(),
        duration_seconds=None if row["duration_seconds"] is None else int(row["duration_seconds"]),
        estimated_start_percent=None
        if row["estimated_start_percent"] is None
        else float(row["estimated_start_percent"]),
        estimated_end_percent=None
        if row["estimated_end_percent"] is None
        else float(row["estimated_end_percent"]),
        power_source=str(row["power_source"]),
        uncertain=bool(row["uncertain"]),
        start_reason=str(row["start_reason"]),
    )


def ensure_config(connection, now: datetime) -> None:
    connection.execute(
        """
        INSERT INTO battery_config (
            id, estimated_full_runtime_minutes, full_charge_time_minutes, updated_at
        ) VALUES (1, NULL, NULL, ?)
        ON CONFLICT(id) DO NOTHING
        """,
        (_iso(now),),
    )


def load_config(connection) -> BatteryConfig:
    row = connection.execute("SELECT * FROM battery_config WHERE id = 1").fetchone()
    if row is None:
        return BatteryConfig(None, None)
    runtime = row["estimated_full_runtime_minutes"]
    charge = row["full_charge_time_minutes"]
    return BatteryConfig(
        None if runtime is None else float(runtime),
        None if charge is None else float(charge),
    )


def get_open(connection) -> StoredSession | None:
    rows = connection.execute(
        "SELECT * FROM battery_sessions WHERE ended_at IS NULL ORDER BY id DESC"
    ).fetchall()
    if not rows:
        return None
    for extra in rows[1:]:
        connection.execute(
            """
            UPDATE battery_sessions
            SET ended_at = last_seen_at, duration_seconds = 0
            WHERE id = ?
            """,
            (int(extra["id"]),),
        )
    return _session(rows[0])


def insert_session(
    connection,
    *,
    started_at: datetime,
    power_source: str,
    start_percent: float | None,
    uncertain: bool,
    start_reason: str,
) -> StoredSession:
    stamp = _iso(started_at)
    cursor = connection.execute(
        """
        INSERT INTO battery_sessions (
            started_at, ended_at, last_seen_at, duration_seconds,
            estimated_start_percent, estimated_end_percent,
            power_source, uncertain, start_reason
        ) VALUES (?, NULL, ?, NULL, ?, NULL, ?, ?, ?)
        """,
        (stamp, stamp, start_percent, power_source, 1 if uncertain else 0, start_reason),
    )
    row = connection.execute(
        "SELECT * FROM battery_sessions WHERE id = ?",
        (int(cursor.lastrowid),),
    ).fetchone()
    return _session(row)


def touch(connection, session_id: int, seen_at: datetime) -> None:
    connection.execute(
        "UPDATE battery_sessions SET last_seen_at = ? WHERE id = ?",
        (_iso(seen_at), session_id),
    )


def close_session(
    connection,
    session: StoredSession,
    end_time: datetime,
    end_percent: float | None,
) -> int:
    duration = max(0, int(round((end_time - session.started_at).total_seconds())))
    connection.execute(
        """
        UPDATE battery_sessions
        SET ended_at = ?, last_seen_at = ?, duration_seconds = ?, estimated_end_percent = ?
        WHERE id = ?
        """,
        (_iso(end_time), _iso(end_time), duration, end_percent, session.id),
    )
    return duration


def add_sample(connection, now: datetime, duration_seconds: float, session_id: int, config: BatteryConfig) -> None:
    if not sample_duration_ok(duration_seconds):
        return
    existing = connection.execute(
        "SELECT 1 FROM battery_calibration_samples WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    if existing is not None:
        return
    connection.execute(
        """
        INSERT INTO battery_calibration_samples (recorded_at, duration_seconds, session_id)
        VALUES (?, ?, ?)
        """,
        (_iso(now), int(round(duration_seconds)), session_id),
    )
    rows = connection.execute(
        """
        SELECT duration_seconds FROM battery_calibration_samples
        ORDER BY id DESC LIMIT 5
        """
    ).fetchall()
    minutes = [float(row["duration_seconds"]) / 60 for row in reversed(rows)]
    median = rolling_median(minutes)
    if median is None:
        return
    updated = bound_runtime(config.estimated_full_runtime_minutes, median)
    connection.execute(
        """
        UPDATE battery_config
        SET estimated_full_runtime_minutes = ?, updated_at = ?
        WHERE id = 1
        """,
        (updated, _iso(now)),
    )


def sessions_since(connection, since: datetime) -> list[StoredSession]:
    rows = connection.execute(
        """
        SELECT * FROM battery_sessions
        WHERE power_source = 'battery'
          AND (ended_at IS NULL OR ended_at >= ?)
        ORDER BY id
        """,
        (_iso(since),),
    ).fetchall()
    return [_session(row) for row in rows]


def calibration_summary(connection) -> tuple[int, list[float], float | None]:
    count_row = connection.execute("SELECT COUNT(*) AS count FROM battery_calibration_samples").fetchone()
    rows = connection.execute(
        """
        SELECT duration_seconds FROM battery_calibration_samples
        ORDER BY id DESC LIMIT 5
        """
    ).fetchall()
    minutes = [round(float(row["duration_seconds"]) / 60, 1) for row in reversed(rows)]
    median = rolling_median(minutes)
    return int(count_row["count"]) if count_row else 0, minutes, median


def current_open_session_id() -> int | None:
    with connect() as connection:
        session = get_open(connection)
    return None if session is None else session.id


def get_battery_settings() -> tuple[BatteryConfig, int, list[float], float | None]:
    now = utc_now()
    with connect() as connection:
        ensure_config(connection, now)
        config = load_config(connection)
        count, samples, median = calibration_summary(connection)
    return config, count, samples, median


def save_battery_settings(runtime_minutes: float | None, charge_minutes: float | None) -> None:
    now = utc_now()
    with connect() as connection:
        ensure_config(connection, now)
        connection.execute(
            """
            UPDATE battery_config
            SET estimated_full_runtime_minutes = ?,
                full_charge_time_minutes = ?,
                updated_at = ?
            WHERE id = 1
            """,
            (runtime_minutes, charge_minutes, _iso(now)),
        )


def stats_window_start(now: datetime) -> datetime:
    return now - timedelta(days=8)
