"""Association failures stay unknown, and partial sources stay read-only."""

from __future__ import annotations

import logging
import subprocess

import pytest

from backend.discover_helper import (
    ALLOWED_COMMANDS,
    GENERIC_ERROR,
    HOSTAPD_ERROR,
    MISSING_ERROR,
    PERMISSION_ERROR,
    TIMEOUT_ERROR,
    DiscoverError,
    failure_reason,
    handle_payload,
    run_command,
)
from backend.services.discovery_parse import build_inventory, parse_dhcp_leases, parse_hostapd_stations
from backend.tests.test_devices import HOSTAPD, LEASES, NEIGHBORS, Sources


def test_permission_failure_is_sanitized(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    import backend.discover_helper as helper

    helper._last_failure = ""
    monkeypatch.setattr(helper.os.path, "isfile", lambda _path: True)

    class Completed:
        returncode = 1
        stdout = ""
        stderr = "sudo: a password is required\n/etc/sudoers.d/pideck-discover"

    monkeypatch.setattr(helper.subprocess, "run", lambda *_args, **_kwargs: Completed())
    with caplog.at_level(logging.WARNING):
        with pytest.raises(DiscoverError) as caught:
            run_command(list(ALLOWED_COMMANDS[0]), 1)
    assert caught.value.reason == "permission"
    assert str(caught.value) == PERMISSION_ERROR
    assert "sudoers" not in str(caught.value)
    assert "password" not in str(caught.value)
    assert any("reason=permission" in record.message for record in caplog.records)
    assert any("password is required" in record.message for record in caplog.records)


def test_empty_station_list_is_a_successful_read(monkeypatch: pytest.MonkeyPatch) -> None:
    import backend.discover_helper as helper

    helper._last_failure = ""
    monkeypatch.setattr(helper.os.path, "isfile", lambda _path: True)

    class Completed:
        returncode = 1
        stdout = "Selected interface 'wlan0'\nFAIL\n"
        stderr = ""

    monkeypatch.setattr(helper.subprocess, "run", lambda *_args, **_kwargs: Completed())
    assert failure_reason("", Completed.stdout, 1) is None
    text = run_command(list(ALLOWED_COMMANDS[0]), 1)
    assert parse_hostapd_stations(text) == []
    inventory, _rows = build_inventory([], [], parse_dhcp_leases(LEASES), [], "2026-10-28T00:00:00+00:00")
    assert inventory.hostapd == "ok"
    assert inventory.connected == 0
    assert all(item.status == "offline" for item in inventory.devices)


def test_silent_nonzero_exit_is_not_an_empty_station_list(monkeypatch: pytest.MonkeyPatch) -> None:
    import backend.discover_helper as helper

    helper._last_failure = ""
    monkeypatch.setattr(helper.os.path, "isfile", lambda _path: True)

    class Completed:
        returncode = 1
        stdout = ""
        stderr = ""

    monkeypatch.setattr(helper.subprocess, "run", lambda *_args, **_kwargs: Completed())
    with pytest.raises(DiscoverError) as caught:
        run_command(list(ALLOWED_COMMANDS[0]), 1)
    assert caught.value.reason == "hostapd"
    assert str(caught.value) == HOSTAPD_ERROR


def test_hostapd_connect_failure_stays_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    import backend.discover_helper as helper

    helper._last_failure = ""
    monkeypatch.setattr(helper.os.path, "isfile", lambda _path: True)

    class Completed:
        returncode = 255
        stdout = ""
        stderr = "Failed to connect to hostapd - wpa_ctrl_open: No such file or directory"

    monkeypatch.setattr(helper.subprocess, "run", lambda *_args, **_kwargs: Completed())
    with pytest.raises(DiscoverError) as caught:
        run_command(list(ALLOWED_COMMANDS[0]), 1)
    assert caught.value.reason == "hostapd"
    assert str(caught.value) == HOSTAPD_ERROR
    assert "wpa_ctrl" not in str(caught.value)


def test_association_timeout_is_sanitized(monkeypatch: pytest.MonkeyPatch) -> None:
    import backend.discover_helper as helper

    helper._last_failure = ""
    monkeypatch.setattr(helper.os.path, "isfile", lambda _path: True)

    def explode(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd="hostapd_cli", timeout=5)

    monkeypatch.setattr(helper.subprocess, "run", explode)
    with pytest.raises(DiscoverError) as caught:
        run_command(list(ALLOWED_COMMANDS[0]), 5)
    assert caught.value.reason == "timeout"
    assert str(caught.value) == TIMEOUT_ERROR
    assert "hostapd_cli" not in str(caught.value)


def test_missing_binary_names_the_gap(monkeypatch: pytest.MonkeyPatch) -> None:
    import backend.discover_helper as helper

    helper._last_failure = ""

    def exists(path: str) -> bool:
        return path.endswith("/sudo")

    monkeypatch.setattr(helper.os.path, "isfile", exists)
    with pytest.raises(DiscoverError) as caught:
        run_command(list(ALLOWED_COMMANDS[0]), 1)
    assert caught.value.reason == "missing"
    assert str(caught.value) == MISSING_ERROR


def test_helper_payload_reports_permission_failure() -> None:
    def run(_args: list[str], _timeout: float) -> str:
        raise DiscoverError(PERMISSION_ERROR, "permission")

    result = handle_payload({"op": "stations"}, run, command=list(ALLOWED_COMMANDS[0]))
    assert result["ok"] is False
    assert result["reason"] == "permission"
    assert result["error"] == PERMISSION_ERROR
    assert result["error"] != GENERIC_ERROR or PERMISSION_ERROR == GENERIC_ERROR


def test_missing_discovery_socket_logs_and_stays_unknown(
    tmp_path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import backend.services.discovery as discovery

    monkeypatch.setenv("PIDECK_DISCOVER_SOCKET", str(tmp_path / "missing.sock"))
    discovery._last_hostapd_error = ""
    with caplog.at_level(logging.WARNING):
        snapshot = discovery.LiveSources().read()
    assert snapshot.hostapd is None
    assert "not running" in snapshot.hostapd_error
    assert any("not running" in record.message for record in caplog.records)
    inventory, _rows = build_inventory(
        [],
        None,
        parse_dhcp_leases(LEASES),
        [],
        "2026-10-28T00:00:00+00:00",
        snapshot.hostapd_error,
    )
    assert inventory.connected is None
    assert inventory.hostapd == "unavailable"
    assert all(item.status == "unknown" for item in inventory.devices)


def test_partial_sources_keep_association_and_leave_the_rest_unavailable() -> None:
    inventory, _rows = build_inventory(
        [],
        parse_hostapd_stations(HOSTAPD),
        None,
        None,
        "2026-10-28T00:00:00+00:00",
    )
    assert inventory.hostapd == "ok"
    assert inventory.dhcp == "unavailable"
    assert inventory.neighbors == "unavailable"
    assert inventory.connected == 2
    assert inventory.hostapd_error == ""
    online = [item for item in inventory.devices if item.status == "online"]
    assert {item.mac for item in online} == {"aa:bb:cc:dd:ee:01", "aa:bb:cc:dd:ee:02"}
    assert all(item.status != "unknown" for item in inventory.devices)


def test_api_partial_sources_remain_read_only(auth_client) -> None:
    auth_client.app.state.device_sources = Sources(HOSTAPD, None, NEIGHBORS)
    response = auth_client.get("/api/devices")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["hostapd"] == "ok"
    assert body["dhcp"] == "unavailable"
    assert body["neighbors"] == "ok"
    assert body["connected"] == 2
    assert body["hostapd_error"] == ""
    online = [item["mac"] for item in body["devices"] if item["status"] == "online"]
    assert online == ["aa:bb:cc:dd:ee:01", "aa:bb:cc:dd:ee:02"] or set(online) == {
        "aa:bb:cc:dd:ee:01",
        "aa:bb:cc:dd:ee:02",
    }
