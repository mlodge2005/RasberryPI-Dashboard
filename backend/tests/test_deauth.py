"""One-client disconnect: validation, authorization, and failure handling."""

from __future__ import annotations

import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

from backend.deauth_helper import (
    HOSTAPD_ERROR,
    MAC_ERROR,
    MISSING_ERROR,
    DeauthHelperError,
    command_args,
    command_is_allowed,
    disconnect_command,
    handle_payload,
    main,
    run_command,
)
from backend.services.deauth import DeauthError
from backend.services.discovery_parse import unicast_mac
from backend.tests.constants import ORIGIN
from backend.tests.test_devices import HOSTAPD, LEASES, NEIGHBORS, Sources

ONLINE = "aa:bb:cc:dd:ee:01"
OFFLINE = "aa:bb:cc:dd:ee:03"
ROOT = Path(__file__).resolve().parents[2]
_LOADER = SourceFileLoader("pideck_disconnect_client", str(ROOT / "scripts" / "disconnect-client"))
_SPEC = importlib.util.spec_from_loader(_LOADER.name, _LOADER)
assert _SPEC is not None
disconnect_client = importlib.util.module_from_spec(_SPEC)
_LOADER.exec_module(disconnect_client)


BAD_MACS = (
    "*",
    "",
    "all",
    "ff:ff:ff:ff:ff:ff",
    "FF:FF:FF:FF:FF:FF",
    "01:00:5e:00:00:01",
    "aa:bb:cc:dd:ee:01 ",
    "aa:bb:cc:dd:ee:zz",
    "aa:bb:cc:dd:ee:01:02",
    "not-a-mac",
)


def test_mac_allowlist_rejects_broadcast_multicast_and_wildcards() -> None:
    assert unicast_mac(ONLINE) == ONLINE
    assert unicast_mac("AA-BB-CC-DD-EE-01") == ONLINE
    for value in BAD_MACS:
        assert unicast_mac(value) is None
        assert disconnect_command(value) is None
        assert command_args(value) is None
        assert disconnect_client.command_args(value) is None
    command = disconnect_command(ONLINE)
    assert command == ["/usr/bin/sudo", "-n", "/usr/local/lib/pideck/disconnect-client", ONLINE]
    assert command_is_allowed(command) is True
    assert command_args(ONLINE) == disconnect_client.command_args(ONLINE)
    assert disconnect_client.command_args(ONLINE) == [
        "/usr/sbin/hostapd_cli",
        "-i",
        "wlan0",
        "deauthenticate",
        ONLINE,
    ]
    assert command_is_allowed([*command, "extra"]) is False
    assert command_is_allowed(["/usr/bin/sudo", "-n", "/usr/sbin/hostapd_cli", "-i", "wlan0", "deauthenticate", ONLINE]) is False
    assert command_is_allowed(["/usr/bin/sudo", "-n", "/usr/local/lib/pideck/disconnect-client", "*"]) is False
    assert command_is_allowed(["/usr/bin/sudo", "-n", "/usr/local/lib/pideck/disconnect-client", "ff:ff:ff:ff:ff:ff"]) is False


def test_wrapper_refuses_to_exec_a_bad_mac(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_exec(*_args):
        raise AssertionError("hostapd was executed")

    monkeypatch.setattr(disconnect_client.os, "execv", fail_exec)
    assert disconnect_client.main(["disconnect-client"]) == 2
    assert disconnect_client.main(["disconnect-client", "ff:ff:ff:ff:ff:ff"]) == 2
    assert disconnect_client.main(["disconnect-client", "*"]) == 2
    assert disconnect_client.main(["disconnect-client", ONLINE, "extra"]) == 2


def test_wrapper_execs_only_the_validated_client(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def fake_exec(path: str, args: list[str]) -> None:
        seen["path"] = path
        seen["args"] = args
        raise SystemExit(0)

    monkeypatch.setattr(disconnect_client.os, "execv", fake_exec)
    with pytest.raises(SystemExit) as caught:
        disconnect_client.main(["disconnect-client", ONLINE])
    assert caught.value.code == 0
    assert seen["args"] == [
        "/usr/sbin/hostapd_cli",
        "-i",
        "wlan0",
        "deauthenticate",
        ONLINE,
    ]


def test_helper_accepts_only_one_disconnect_command() -> None:
    seen: dict[str, object] = {}

    def run(args: list[str], timeout: float) -> str:
        seen["args"] = args
        seen["timeout"] = timeout
        return "OK"

    accepted = handle_payload({"op": "disconnect", "mac": ONLINE}, run)
    assert accepted["ok"] is True
    assert seen["args"] == disconnect_command(ONLINE)
    refused = handle_payload({"op": "disconnect", "mac": ONLINE, "all": True}, run)
    assert refused["status_code"] == 404
    broadcast = handle_payload({"op": "disconnect", "mac": "ff:ff:ff:ff:ff:ff"}, run)
    assert broadcast["status_code"] == 400
    assert broadcast["error"] == MAC_ERROR
    wildcard = handle_payload({"op": "disconnect", "mac": "*"}, run)
    assert wildcard["ok"] is False
    stations = handle_payload({"op": "stations"}, run)
    assert stations["status_code"] == 404
    assert main(["--socket", "/run/pideck/deauth.sock"]) == 1


def test_missing_disconnect_program_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    import backend.deauth_helper as helper

    monkeypatch.setattr(helper.os.path, "isfile", lambda _path: False)
    command = disconnect_command(ONLINE)
    assert command is not None
    with pytest.raises(DeauthHelperError) as caught:
        run_command(command, 1)
    assert caught.value.reason == "missing"
    assert str(caught.value) == MISSING_ERROR


def test_disconnect_failure_is_sanitized(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    import logging

    import backend.deauth_helper as helper

    helper._last_failure = ""
    monkeypatch.setattr(helper.os.path, "isfile", lambda _path: True)

    class Completed:
        returncode = 1
        stdout = ""
        stderr = "sudo: a password is required"

    monkeypatch.setattr(helper.subprocess, "run", lambda *_args, **_kwargs: Completed())
    command = disconnect_command(ONLINE)
    assert command is not None
    with caplog.at_level(logging.WARNING):
        with pytest.raises(DeauthHelperError) as caught:
            run_command(command, 1)
    assert caught.value.reason == "permission"
    assert "password" not in str(caught.value)
    assert any("reason=permission" in record.message for record in caplog.records)

    class Hostapd:
        returncode = 1
        stdout = "FAIL"
        stderr = ""

    monkeypatch.setattr(helper.subprocess, "run", lambda *_args, **_kwargs: Hostapd())
    helper._last_failure = ""
    with pytest.raises(DeauthHelperError) as hostapd_failure:
        run_command(command, 1)
    assert hostapd_failure.value.reason == "hostapd"
    assert str(hostapd_failure.value) == HOSTAPD_ERROR


class Recorder:
    def __init__(self, fail: DeauthError | None = None) -> None:
        self.macs: list[str] = []
        self.fail = fail

    def disconnect(self, mac: str) -> None:
        if self.fail is not None:
            raise self.fail
        self.macs.append(mac)


def test_disconnect_requires_a_session(client) -> None:
    assert client.post(f"/api/devices/{ONLINE}/disconnect", headers=ORIGIN).status_code == 401


def test_disconnect_requires_origin(auth_client) -> None:
    auth_client.app.state.device_sources = Sources(HOSTAPD, LEASES, NEIGHBORS)
    missing_origin = auth_client.post(f"/api/devices/{ONLINE}/disconnect")
    assert missing_origin.status_code == 403
    evil = auth_client.post(
        f"/api/devices/{ONLINE}/disconnect",
        headers={"Origin": "https://evil.example"},
    )
    assert evil.status_code == 403


def test_disconnect_one_associated_client(auth_client) -> None:
    auth_client.app.state.device_sources = Sources(HOSTAPD, LEASES, NEIGHBORS)
    recorder = Recorder()
    auth_client.app.state.deauth_client = recorder
    assert auth_client.get("/api/devices").status_code == 200
    saved = auth_client.put(f"/api/devices/{ONLINE}", json={"alias": "Alfred"}, headers=ORIGIN)
    assert saved.status_code == 200, saved.text
    response = auth_client.post(f"/api/devices/{ONLINE.upper()}/disconnect", headers=ORIGIN)
    assert response.status_code == 200, response.text
    body = response.json()
    assert recorder.macs == [ONLINE]
    assert "reconnect" in body["detail"]
    assert "Block" not in body["detail"]
    kept = next(item for item in body["inventory"]["devices"] if item["mac"] == ONLINE)
    assert kept["alias"] == "Alfred"


def test_disconnect_rejects_clients_that_are_not_associated(auth_client) -> None:
    auth_client.app.state.device_sources = Sources(HOSTAPD, LEASES, NEIGHBORS)
    recorder = Recorder()
    auth_client.app.state.deauth_client = recorder
    assert auth_client.get("/api/devices").status_code == 200
    offline = auth_client.post(f"/api/devices/{OFFLINE}/disconnect", headers=ORIGIN)
    assert offline.status_code == 409
    assert recorder.macs == []
    for mac in ("ff:ff:ff:ff:ff:ff", "01:00:5e:00:00:01", "not-a-mac", "%2A"):
        rejected = auth_client.post(f"/api/devices/{mac}/disconnect", headers=ORIGIN)
        assert rejected.status_code == 400, mac
    assert recorder.macs == []


def test_disconnect_is_refused_when_association_is_unknown(auth_client) -> None:
    auth_client.app.state.device_sources = Sources(HOSTAPD, LEASES, NEIGHBORS)
    assert auth_client.get("/api/devices").status_code == 200
    auth_client.app.state.device_sources = Sources(
        None,
        LEASES,
        NEIGHBORS,
        "Association data is unavailable. The discovery helper is not running.",
    )
    recorder = Recorder()
    auth_client.app.state.deauth_client = recorder
    response = auth_client.post(f"/api/devices/{ONLINE}/disconnect", headers=ORIGIN)
    assert response.status_code == 503
    assert "not running" in response.json()["detail"]
    assert "offline" not in response.json()["detail"].lower()
    assert recorder.macs == []
    listed = auth_client.get("/api/devices")
    assert all(item["status"] == "unknown" for item in listed.json()["devices"])


def test_disconnect_failure_leaves_the_device_in_place(auth_client) -> None:
    auth_client.app.state.device_sources = Sources(HOSTAPD, LEASES, NEIGHBORS)
    auth_client.app.state.deauth_client = Recorder(DeauthError(HOSTAPD_ERROR, 503))
    response = auth_client.post(f"/api/devices/{ONLINE}/disconnect", headers=ORIGIN)
    assert response.status_code == 503
    assert response.json()["detail"] == HOSTAPD_ERROR
    listed = auth_client.get("/api/devices")
    device = next(item for item in listed.json()["devices"] if item["mac"] == ONLINE)
    assert device["status"] == "online"


def test_missing_deauth_socket_is_a_controlled_error(auth_client, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PIDECK_DEAUTH_SOCKET", str(tmp_path / "missing.sock"))
    auth_client.app.state.device_sources = Sources(HOSTAPD, LEASES, NEIGHBORS)
    response = auth_client.post(f"/api/devices/{ONLINE}/disconnect", headers=ORIGIN)
    assert response.status_code == 503
    assert response.json()["detail"] == "Disconnect is unavailable. The disconnect helper is not running."
    assert "missing.sock" not in response.text
