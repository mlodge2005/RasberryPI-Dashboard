"""Hotspot discovery parsing, persistence, and the read-only helper boundary."""

from __future__ import annotations

from pathlib import Path

from backend.discover_helper import (
    ALLOWED_COMMANDS,
    DiscoverError,
    handle_payload,
    is_production_socket,
    main,
    run_command,
)
from backend.services.discovery import SourceSnapshot
from backend.services.discovery_parse import (
    build_inventory,
    parse_dhcp_leases,
    parse_hostapd_stations,
    parse_neighbors,
)
from backend.tests.constants import ORIGIN

HOSTAPD = """Selected interface 'wlan0'
aa:bb:cc:dd:ee:01
flags=[AUTH][ASSOC][AUTHORIZED]
connected_time=90
rx_bytes=1000
tx_bytes=2000
rx_packets=10
tx_packets=20
signal=-48

aa-bb-cc-dd-ee-02
flags=[AUTH][ASSOC]
connected_time=5
signal=-70
"""

LEASES = """1730000000 aa:bb:cc:dd:ee:01 10.42.0.51 phone *
1730000000 AA-BB-CC-DD-EE-03 10.42.0.52 * 01:aa
1730000000 aa:bb:cc:dd:ee:05 10.42.0.55 phone client
not a lease
"""

NEIGHBORS = """10.42.0.51 lladdr aa:bb:cc:dd:ee:01 REACHABLE
10.42.0.54 lladdr aa:bb:cc:dd:ee:04 STALE
10.42.0.9 dev wlan1 lladdr aa:bb:cc:dd:ee:09 REACHABLE
10.42.0.8 INCOMPLETE
"""


def test_hostapd_parser_reads_association_counters() -> None:
    stations = {item.mac: item for item in parse_hostapd_stations(HOSTAPD)}
    assert set(stations) == {"aa:bb:cc:dd:ee:01", "aa:bb:cc:dd:ee:02"}
    first = stations["aa:bb:cc:dd:ee:01"]
    assert first.authorized is True
    assert first.connected_seconds == 90
    assert first.signal_dbm == -48
    assert first.rx_bytes == 1000
    assert first.tx_bytes == 2000
    assert first.rx_packets == 10
    assert first.tx_packets == 20
    assert stations["aa:bb:cc:dd:ee:02"].authorized is False


def test_dhcp_and_neighbor_parsers_keep_only_usable_records() -> None:
    leases = parse_dhcp_leases(LEASES)
    assert [(item.mac, item.ip_address, item.hostname) for item in leases] == [
        ("aa:bb:cc:dd:ee:01", "10.42.0.51", "phone"),
        ("aa:bb:cc:dd:ee:03", "10.42.0.52", ""),
        ("aa:bb:cc:dd:ee:05", "10.42.0.55", "phone"),
    ]
    neighbors = parse_neighbors(NEIGHBORS)
    assert [(item.mac, item.ip_address) for item in neighbors] == [
        ("aa:bb:cc:dd:ee:01", "10.42.0.51"),
        ("aa:bb:cc:dd:ee:04", "10.42.0.54"),
    ]


def test_merge_marks_only_associated_devices_online() -> None:
    inventory, _rows = build_inventory(
        [],
        parse_hostapd_stations(HOSTAPD),
        parse_dhcp_leases(LEASES),
        parse_neighbors(NEIGHBORS),
        "2026-10-28T00:00:00+00:00",
    )
    by_mac = {item.mac: item for item in inventory.devices}
    assert by_mac["aa:bb:cc:dd:ee:01"].status == "online"
    assert by_mac["aa:bb:cc:dd:ee:01"].hostname == "phone"
    assert by_mac["aa:bb:cc:dd:ee:01"].ip_address == "10.42.0.51"
    assert by_mac["aa:bb:cc:dd:ee:02"].status == "online"
    assert by_mac["aa:bb:cc:dd:ee:03"].status == "offline"
    assert by_mac["aa:bb:cc:dd:ee:04"].status == "offline"
    assert by_mac["aa:bb:cc:dd:ee:04"].ip_address == "10.42.0.54"
    assert "aa:bb:cc:dd:ee:09" not in by_mac
    assert inventory.connected == 2
    assert inventory.known == 5
    assert inventory.hostapd_error == ""
    phones = [item for item in inventory.devices if item.hostname == "phone"]
    assert {item.mac for item in phones} == {"aa:bb:cc:dd:ee:01", "aa:bb:cc:dd:ee:05"}
    assert all(item.kind == "phone" for item in phones)
    assert by_mac["aa:bb:cc:dd:ee:02"].kind == "generic"
    assert inventory.unidentified == 3


def test_failed_hostapd_does_not_invent_offline_devices() -> None:
    first, stored = build_inventory(
        [],
        parse_hostapd_stations(HOSTAPD),
        parse_dhcp_leases(LEASES),
        [],
        "2026-10-28T00:00:00+00:00",
    )
    seen = next(item.last_seen for item in first.devices if item.mac == "aa:bb:cc:dd:ee:01")
    second, kept = build_inventory(stored, None, parse_dhcp_leases(LEASES), [], "2026-10-28T01:00:00+00:00")
    again = next(item for item in second.devices if item.mac == "aa:bb:cc:dd:ee:01")
    assert again.status == "unknown"
    assert again.status != "offline"
    assert again.last_seen == seen
    assert second.hostapd == "unavailable"
    assert second.connected is None
    assert second.hostapd_error == "Association data is unavailable."
    assert all(item.status == "unknown" for item in second.devices)
    assert all(item.last_seen == "2026-10-28T00:00:00+00:00" for item in kept)


class Sources:
    def __init__(
        self,
        hostapd: str | None,
        dhcp: str | None,
        neighbors: str | None,
        error: str = "",
    ) -> None:
        self.snapshot = SourceSnapshot(hostapd, dhcp, neighbors, error)

    def read(self) -> SourceSnapshot:
        return self.snapshot


def test_inventory_persists_and_alias_survives_refresh(auth_client) -> None:
    auth_client.app.state.device_sources = Sources(HOSTAPD, LEASES, NEIGHBORS)
    created = auth_client.get("/api/devices")
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["connected"] == 2
    assert body["known"] == 5
    saved = auth_client.put(
        "/api/devices/AA:BB:CC:DD:EE:03",
        json={"alias": "Bench phone"},
        headers=ORIGIN,
    )
    assert saved.status_code == 200, saved.text
    renamed = next(item for item in saved.json()["devices"] if item["mac"] == "aa:bb:cc:dd:ee:03")
    assert renamed["alias"] == "Bench phone"
    assert renamed["status"] == "offline"
    again = auth_client.post("/api/devices/refresh", headers=ORIGIN)
    assert again.status_code == 200, again.text
    kept = next(item for item in again.json()["devices"] if item["mac"] == "aa:bb:cc:dd:ee:03")
    assert kept["alias"] == "Bench phone"
    cleared = auth_client.put(
        "/api/devices/aa:bb:cc:dd:ee:03",
        json={"alias": ""},
        headers=ORIGIN,
    )
    assert cleared.status_code == 200
    blank = next(item for item in cleared.json()["devices"] if item["mac"] == "aa:bb:cc:dd:ee:03")
    assert blank["alias"] == ""


def test_missing_sources_leave_known_devices_in_place(auth_client) -> None:
    auth_client.app.state.device_sources = Sources(HOSTAPD, LEASES, NEIGHBORS)
    assert auth_client.get("/api/devices").status_code == 200
    auth_client.app.state.device_sources = Sources(None, None, None)
    response = auth_client.get("/api/devices")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["hostapd"] == "unavailable"
    assert body["dhcp"] == "unavailable"
    assert body["neighbors"] == "unavailable"
    assert body["known"] == 5
    assert body["connected"] is None
    assert body["hostapd_error"] == "Association data is unavailable."
    assert all(item["status"] == "unknown" for item in body["devices"])
    assert all(item["status"] != "offline" for item in body["devices"])


def test_device_routes_require_a_session(client) -> None:
    assert client.get("/api/devices").status_code == 401
    assert client.post("/api/devices/refresh", headers=ORIGIN).status_code == 401
    assert client.put("/api/devices/aa:bb:cc:dd:ee:01", json={"alias": "Phone"}, headers=ORIGIN).status_code == 401


def test_alias_validation(auth_client) -> None:
    missing = auth_client.put(
        "/api/devices/aa:bb:cc:dd:ee:01",
        json={"alias": "Phone"},
        headers=ORIGIN,
    )
    assert missing.status_code == 404
    invalid = auth_client.put("/api/devices/not-a-mac", json={"alias": "Phone"}, headers=ORIGIN)
    assert invalid.status_code == 400
    control = auth_client.put(
        "/api/devices/aa:bb:cc:dd:ee:01",
        json={"alias": "bad\nname"},
        headers=ORIGIN,
    )
    assert control.status_code == 422


def test_helper_accepts_only_the_association_command() -> None:
    seen: dict[str, object] = {}

    def run(args: list[str], timeout: float) -> str:
        seen["args"] = args
        seen["timeout"] = timeout
        return HOSTAPD

    result = handle_payload({"op": "stations"}, run, command=list(ALLOWED_COMMANDS[0]))
    assert result["ok"] is True
    assert seen["args"] == list(ALLOWED_COMMANDS[0])
    assert all(command[4:] == ["wlan0", "all_sta"] for command in ALLOWED_COMMANDS)
    assert all(command[:2] == ["/usr/bin/sudo", "-n"] for command in ALLOWED_COMMANDS)
    refused = handle_payload({"op": "stations", "interface": "wlan1"}, run, command=list(ALLOWED_COMMANDS[0]))
    assert refused["ok"] is False
    deauth = handle_payload({"op": "deauthenticate"}, run, command=list(ALLOWED_COMMANDS[0]))
    assert deauth["status_code"] == 404
    try:
        run_command(["/usr/bin/sudo", "-n", "/usr/sbin/hostapd_cli", "-i", "wlan0", "deauthenticate"], 1)
    except DiscoverError:
        pass
    else:
        raise AssertionError("deauthenticate was accepted")


def test_discovery_unit_and_sudoers_stay_read_only() -> None:
    root = Path(__file__).resolve().parents[2]
    unit = (root / "systemd" / "pideck-discover.service").read_text(encoding="utf-8")
    socket = (root / "systemd" / "pideck-discover.socket").read_text(encoding="utf-8")
    sudoers = (root / "sudoers" / "pideck-discover").read_text(encoding="utf-8")
    assert "User=pideck-discover\n" in unit
    assert "NoNewPrivileges=" not in unit
    assert "LoadCredential" not in unit
    assert "ProtectSystem=strict\n" in unit
    assert "InaccessiblePaths=/etc/pideck /var/lib/pideck\n" in unit
    assert "wlan1" not in unit
    assert "deauthenticate" not in unit.lower()
    assert "PrivateTmp=yes" not in unit
    assert "PrivateTmp=no\n" in unit
    assert "/tmp" in unit
    assert "ListenStream=/run/pideck/discover.sock\n" in socket
    assert "SocketMode=0600\n" in socket
    assert "SocketUser=pideck\n" in socket
    assert "SocketGroup=pideck\n" in socket
    assert [line for line in sudoers.splitlines() if line.strip()][-1] == (
        "pideck-discover ALL=(root) NOPASSWD: /usr/sbin/hostapd_cli -i wlan0 all_sta"
    )
    assert "/usr/bin/hostapd_cli" not in sudoers
    assert "*" not in sudoers
    assert "NOPASSWD: ALL" not in sudoers
    assert "/bin/sh" not in sudoers
    assert "/bin/bash" not in sudoers
    assert "deauthenticate" not in sudoers
    assert "!use_pty" in sudoers
    assert "wlan1" not in sudoers
    deauth_unit = (root / "systemd" / "pideck-deauth.service").read_text(encoding="utf-8")
    deauth_socket = (root / "systemd" / "pideck-deauth.socket").read_text(encoding="utf-8")
    deauth_sudoers = (root / "sudoers" / "pideck-deauth").read_text(encoding="utf-8")
    assert "User=pideck-deauth\n" in deauth_unit
    assert "NoNewPrivileges=" not in deauth_unit
    assert "PrivateTmp=no\n" in deauth_unit
    assert "PrivateTmp=yes" not in deauth_unit
    assert "all_sta" not in deauth_unit
    assert "ListenStream=/run/pideck/deauth.sock\n" in deauth_socket
    assert "SocketMode=0600\n" in deauth_socket
    assert "SocketUser=pideck\n" in deauth_socket
    assert "FileDescriptorName=deauth\n" in deauth_socket
    assert "*" not in deauth_sudoers
    assert "hostapd_cli" not in deauth_sudoers
    assert "NOPASSWD: ALL" not in deauth_sudoers
    assert "/bin/sh" not in deauth_sudoers
    assert "deauthenticate" not in deauth_sudoers
    assert "disconnect-client [0-9a-f][0-9a-f]:" in deauth_sudoers
    assert ALLOWED_COMMANDS == (
        ["/usr/bin/sudo", "-n", "/usr/sbin/hostapd_cli", "-i", "wlan0", "all_sta"],
    )
    accounts = (root / "scripts" / "ensure-accounts.sh").read_text(encoding="utf-8")
    assert accounts.index("The discovery sudoers file is missing") < accounts.index("sudo visudo -cf")
    assert accounts.index("sudo visudo -cf") < accounts.index('sudo cp "$unit_tmp"')
    assert accounts.index("install_discovery_sudoers") < accounts.index("systemctl enable")
    assert accounts.index("install_deauth_sudoers") < accounts.index("systemctl enable")
    assert accounts.index("install_deauth_wrapper") < accounts.index("systemctl enable")
    commands = "\n".join(line for line in accounts.splitlines() if not line.strip().startswith("#"))
    for forbidden in (
        "systemctl restart hostapd",
        "systemctl restart dnsmasq",
        "systemctl restart NetworkManager",
        "systemctl restart tailscaled",
        "nft",
    ):
        assert forbidden not in commands
    assert is_production_socket("/run/pideck/discover.sock") is True
    assert main(["--socket", "/run/pideck/discover.sock"]) == 1
