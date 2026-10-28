"""Parsers for hotspot association, DHCP leases, and the neighbor table.

A device is Connected only when hostapd lists its MAC. A DHCP lease or a
neighbor entry supplies an address and a hostname, and nothing more. When
hostapd cannot be read, every device stays Unknown and the connected count
is omitted instead of reported as zero.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from backend.models.schemas import DeviceInventory, DeviceKind, DeviceRecord

HOTSPOT_IFACE = "wlan0"
_MAC = re.compile(r"^[0-9a-f]{2}(?::[0-9a-f]{2}){5}$")
_IPV4 = re.compile(r"^(?:\d{1,3}\.){3}\d{1,3}$")
_INT = re.compile(r"-?\d+")


@dataclass(frozen=True)
class Station:
    mac: str
    authorized: bool
    connected_seconds: int | None
    signal_dbm: int | None
    rx_bytes: int | None
    tx_bytes: int | None
    rx_packets: int | None
    tx_packets: int | None


@dataclass(frozen=True)
class Lease:
    mac: str
    ip_address: str
    hostname: str


@dataclass(frozen=True)
class Neighbor:
    mac: str
    ip_address: str


@dataclass(frozen=True)
class StoredDevice:
    mac: str
    alias: str
    hostname: str
    ip_address: str
    first_seen: str
    last_seen: str


def normalize_mac(value: str) -> str | None:
    text = value.strip().lower().replace("-", ":")
    if _MAC.fullmatch(text):
        return text
    return None


def unicast_mac(value: str) -> str | None:
    """Return a canonical MAC that addresses one station.

    Multicast and broadcast addresses are rejected. hostapd treats
    ff:ff:ff:ff:ff:ff as every associated client. Surrounding whitespace is
    rejected so a padded value cannot widen the command that sudo runs.
    """
    if value != value.strip() or any(char.isspace() for char in value):
        return None
    mac = normalize_mac(value)
    if mac is None:
        return None
    if int(mac[:2], 16) & 0x01:
        return None
    return mac


_KIND_WORDS: tuple[tuple[DeviceKind, tuple[str, ...]], ...] = (
    ("phone", ("iphone", "android", "pixel", "galaxy", "phone")),
    ("tablet", ("ipad", "tablet")),
    ("watch", ("watch",)),
    ("tv", ("chromecast", "roku", "bravia", "firetv", "tv")),
    ("laptop", ("macbook", "laptop", "notebook")),
    ("desktop", ("imac", "desktop", "workstation")),
)


def device_kind(alias: str, hostname: str) -> DeviceKind:
    haystack = f" {alias} {hostname} ".lower()
    for kind, words in _KIND_WORDS:
        for word in words:
            if re.search(rf"(^|[^a-z0-9]){re.escape(word)}([^a-z0-9]|$)", haystack):
                return kind
    return "generic"


def public_hostapd_error(value: str) -> str:
    text = " ".join(value.split())
    if not text.startswith("Association data is unavailable"):
        return "Association data is unavailable."
    if len(text) > 200 or any(ord(char) < 32 or ord(char) == 127 for char in text):
        return "Association data is unavailable."
    return text


def _ipv4(value: str) -> str | None:
    if not _IPV4.fullmatch(value):
        return None
    parts = [int(part) for part in value.split(".")]
    if any(part > 255 for part in parts):
        return None
    return value


def _number(value: str) -> int | None:
    match = _INT.search(value.strip())
    if match is None:
        return None
    return int(match.group(0))


def _count(value: str) -> int | None:
    number = _number(value)
    if number is None or number < 0:
        return None
    return number


def _hostname(value: str) -> str:
    text = value.strip()
    if text in {"", "*"}:
        return ""
    if any(ord(char) < 32 or ord(char) == 127 for char in text):
        return ""
    return text[:64]


def parse_hostapd_stations(text: str) -> list[Station]:
    """Parse `hostapd_cli -i wlan0 all_sta`. Stations are separated by blanks."""
    stations: list[Station] = []
    current_mac = ""
    fields: dict[str, str] = {}

    def finish() -> None:
        nonlocal current_mac, fields
        if current_mac:
            flags = fields.get("flags", "")
            stations.append(
                Station(
                    mac=current_mac,
                    authorized="[AUTHORIZED]" in flags or fields.get("authorized") == "1",
                    connected_seconds=_count(fields["connected_time"]) if "connected_time" in fields else None,
                    signal_dbm=_number(fields["signal"]) if "signal" in fields else None,
                    rx_bytes=_count(fields["rx_bytes"]) if "rx_bytes" in fields else None,
                    tx_bytes=_count(fields["tx_bytes"]) if "tx_bytes" in fields else None,
                    rx_packets=_count(fields["rx_packets"]) if "rx_packets" in fields else None,
                    tx_packets=_count(fields["tx_packets"]) if "tx_packets" in fields else None,
                )
            )
        current_mac = ""
        fields = {}

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            finish()
            continue
        mac = normalize_mac(line)
        if mac is not None:
            finish()
            current_mac = mac
            continue
        if "=" not in line or not current_mac:
            continue
        key, value = line.split("=", 1)
        fields[key.strip()] = value.strip()
    finish()
    return stations


def parse_dhcp_leases(text: str) -> list[Lease]:
    """Parse dnsmasq lease lines: expiry, MAC, IP, hostname, client-id."""
    leases: list[Lease] = []
    for raw in text.splitlines():
        parts = raw.split()
        if len(parts) < 4:
            continue
        mac = normalize_mac(parts[1])
        ip_address = _ipv4(parts[2])
        if mac is None or ip_address is None:
            continue
        leases.append(Lease(mac=mac, ip_address=ip_address, hostname=_hostname(parts[3])))
    return leases


def parse_neighbors(text: str) -> list[Neighbor]:
    """Parse `ip -4 neigh show dev wlan0`. Drop any line that names another device."""
    neighbors: list[Neighbor] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or "lladdr" not in line.split():
            continue
        tokens = line.split()
        if "dev" in tokens:
            device = tokens[tokens.index("dev") + 1] if tokens.index("dev") + 1 < len(tokens) else ""
            if device != HOTSPOT_IFACE:
                continue
        mac_at = tokens.index("lladdr") + 1
        if mac_at >= len(tokens):
            continue
        mac = normalize_mac(tokens[mac_at])
        ip_address = _ipv4(tokens[0])
        if mac is None or ip_address is None:
            continue
        neighbors.append(Neighbor(mac=mac, ip_address=ip_address))
    return neighbors


def build_inventory(
    stored: list[StoredDevice],
    stations: list[Station] | None,
    leases: list[Lease] | None,
    neighbors: list[Neighbor] | None,
    now: str,
    hostapd_error: str = "",
) -> tuple[DeviceInventory, list[StoredDevice]]:
    """Join every source by MAC and return the API view plus rows to persist.

    `None` for a source means that source could not be read. A failed hostapd
    read leaves association unknown and does not rewrite last-seen times.
    The connected count is None in that case so callers cannot show zero.
    """
    rows = {item.mac: item for item in stored}
    lease_by_mac = {item.mac: item for item in leases or []}
    neighbor_by_mac = {item.mac: item for item in neighbors or []}
    station_by_mac = {item.mac: item for item in stations or []}
    observed = set(lease_by_mac) | set(neighbor_by_mac) | set(station_by_mac)

    for mac in observed:
        previous = rows.get(mac)
        lease = lease_by_mac.get(mac)
        neighbor = neighbor_by_mac.get(mac)
        hostname = previous.hostname if previous else ""
        if lease is not None and lease.hostname:
            hostname = lease.hostname
        ip_address = previous.ip_address if previous else ""
        if lease is not None:
            ip_address = lease.ip_address
        elif neighbor is not None and neighbor.ip_address:
            ip_address = neighbor.ip_address
        if previous is None:
            rows[mac] = StoredDevice(
                mac=mac,
                alias="",
                hostname=hostname,
                ip_address=ip_address,
                first_seen=now,
                last_seen=now,
            )
            continue
        last_seen = previous.last_seen
        if stations is not None and mac in station_by_mac:
            last_seen = now
        rows[mac] = StoredDevice(
            mac=mac,
            alias=previous.alias,
            hostname=hostname,
            ip_address=ip_address,
            first_seen=previous.first_seen,
            last_seen=last_seen,
        )

    devices: list[DeviceRecord] = []
    for row in rows.values():
        station = station_by_mac.get(row.mac) if stations is not None else None
        if stations is None:
            status = "unknown"
        elif station is not None:
            status = "online"
        else:
            status = "offline"
        devices.append(
            DeviceRecord(
                mac=row.mac,
                alias=row.alias,
                hostname=row.hostname,
                ip_address=row.ip_address or None,
                status=status,
                kind=device_kind(row.alias, row.hostname),
                first_seen=row.first_seen,
                last_seen=row.last_seen,
                authorized=station.authorized if station else None,
                connected_seconds=station.connected_seconds if station else None,
                signal_dbm=station.signal_dbm if station else None,
                rx_bytes=station.rx_bytes if station else None,
                tx_bytes=station.tx_bytes if station else None,
                rx_packets=station.rx_packets if station else None,
                tx_packets=station.tx_packets if station else None,
            )
        )
    devices.sort(key=lambda item: item.mac)
    devices.sort(key=lambda item: item.last_seen, reverse=True)
    devices.sort(key=lambda item: item.status != "online")
    association_known = stations is not None
    inventory = DeviceInventory(
        devices=devices,
        connected=sum(1 for item in devices if item.status == "online") if association_known else None,
        known=len(devices),
        unidentified=sum(1 for item in devices if not item.alias and not item.hostname),
        hostapd="ok" if association_known else "unavailable",
        dhcp="ok" if leases is not None else "unavailable",
        neighbors="ok" if neighbors is not None else "unavailable",
        hostapd_error="" if association_known else public_hostapd_error(hostapd_error),
    )
    return inventory, list(rows.values())
