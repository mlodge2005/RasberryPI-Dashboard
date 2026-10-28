"""Persistent hotspot device identities. Live association is not stored here."""

from __future__ import annotations

from backend.database import connect
from backend.services.discovery_parse import StoredDevice, normalize_mac


def list_devices() -> list[StoredDevice]:
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT mac, alias, hostname, ip_address, first_seen, last_seen
            FROM devices
            ORDER BY mac
            """
        ).fetchall()
    return [
        StoredDevice(
            mac=str(row["mac"]),
            alias=str(row["alias"]),
            hostname=str(row["hostname"]),
            ip_address=str(row["ip_address"]),
            first_seen=str(row["first_seen"]),
            last_seen=str(row["last_seen"]),
        )
        for row in rows
    ]


def save_devices(rows: list[StoredDevice]) -> None:
    with connect() as connection:
        for row in rows:
            connection.execute(
                """
                INSERT INTO devices (mac, alias, hostname, ip_address, first_seen, last_seen)
                VALUES (?, '', ?, ?, ?, ?)
                ON CONFLICT(mac) DO UPDATE SET
                    hostname = excluded.hostname,
                    ip_address = excluded.ip_address,
                    first_seen = devices.first_seen,
                    last_seen = excluded.last_seen
                """,
                (row.mac, row.hostname, row.ip_address, row.first_seen, row.last_seen),
            )


def set_alias(mac: str, alias: str) -> bool:
    normalized = normalize_mac(mac)
    if normalized is None:
        return False
    with connect() as connection:
        cursor = connection.execute(
            "UPDATE devices SET alias = ? WHERE mac = ?",
            (alias, normalized),
        )
    return cursor.rowcount == 1
