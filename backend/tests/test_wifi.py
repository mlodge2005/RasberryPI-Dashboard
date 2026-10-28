from __future__ import annotations

import json
import socket
import threading
from pathlib import Path

from backend.services.wifi_nm import (
    NMCLI_BIN,
    WifiError,
    command_is_allowed,
    connection_list_command,
    connectivity_command,
    device_show_command,
    device_status_command,
    disconnect_command,
    execute,
    open_connect_command,
    profile_interface_command,
    profile_property_command,
    profile_ssid_command,
    public_nmcli_error,
    reconnect_command,
    secured_connect_command,
    wifi_list_command,
)
from backend.tests.constants import ORIGIN
from backend.wifi_helper import handle_connection, handle_payload, is_production_socket, main, prompt_kind

HOME = "11111111-1111-1111-1111-111111111111"
CAFE = "44444444-4444-4444-4444-444444444444"
OTHER = "33333333-3333-3333-3333-333333333333"
TRAP = "55555555-5555-5555-5555-555555555555"
WIFI_LIST = (
    "*:Home:72:WPA2\n"
    " :Cafe:40:WPA3\n"
    " :CYBERDECK:90:WPA2\n"
    " :Corp:55:WPA2 802.1X\n"
    " :OpenNet:30:\n"
    " :Weird\\:Name:15:WPA2 WPA3\n"
)
DEVICE_STATUS = "wlan0:wifi:unmanaged:\nwlan1:wifi:connected:Home\n"
DEVICE_SHOW = (
    "IP4.ADDRESS[1]:192.168.4.20/24\n"
    "IP4.GATEWAY:192.168.4.1\n"
    "IP4.DNS[1]:192.168.4.1\n"
    "IP4.DNS[2]:1.1.1.1\n"
)
CONNECTIONS = (
    f"Home:{HOME}:802-11-wireless:wlan1\n"
    f"Other:{OTHER}:802-11-wireless:eth0\n"
    f"Trap:{TRAP}:802-11-wireless:wlan0\n"
    f"Cafe:{CAFE}:802-11-wireless:\n"
)


class Script:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.replies = {
            tuple(connection_list_command()): CONNECTIONS,
            tuple(profile_ssid_command(HOME)): "Home\n",
            tuple(profile_interface_command(HOME)): "wlan1\n",
            tuple(profile_ssid_command(CAFE)): "Cafe\n",
            tuple(profile_interface_command(CAFE)): "\n",
            tuple(profile_ssid_command(OTHER)): "Other\n",
            tuple(profile_interface_command(OTHER)): "eth0\n",
            tuple(device_status_command()): DEVICE_STATUS,
            tuple(device_show_command()): DEVICE_SHOW,
            tuple(connectivity_command()): "portal\n",
            tuple(wifi_list_command(rescan=False)): WIFI_LIST,
            tuple(wifi_list_command(rescan=True)): WIFI_LIST,
            tuple(disconnect_command()): "",
            tuple(open_connect_command("OpenNet")): "",
            tuple(secured_connect_command("Cafe")): "",
            tuple(reconnect_command(CAFE)): "",
            tuple(profile_property_command(CAFE, "802-11-wireless.mac-address")): "\n",
            tuple(profile_property_command(CAFE, "connection.master")): "\n",
            tuple(profile_property_command(CAFE, "connection.slave-type")): "\n",
            tuple(profile_property_command(CAFE, "connection.secondaries")): "\n",
        }
        self.secrets: list[str] = []

    def __call__(self, args: list[str], timeout: float) -> str:
        self.calls.append(list(args))
        key = tuple(args)
        if key not in self.replies:
            raise AssertionError(args)
        return self.replies[key]

    def submit_secret(self, args: list[str], secret: str, timeout: float) -> str:
        assert secret not in args
        assert "password" not in args
        self.secrets.append(secret)
        return self(args, timeout)


def test_commands_stay_on_the_upstream_adapter() -> None:
    assert disconnect_command() == [NMCLI_BIN, "device", "disconnect", "wlan1"]
    secured = secured_connect_command("Cafe")
    assert secured[secured.index("ifname") + 1] == "wlan1"
    assert "--ask" in secured
    assert "password" not in secured
    assert "correct-horse" not in secured
    assert command_is_allowed([NMCLI_BIN, "radio", "wifi", "off"]) is False
    assert command_is_allowed([NMCLI_BIN, "networking", "off"]) is False
    assert command_is_allowed([NMCLI_BIN, "device", "disconnect", "wlan0"]) is False
    assert command_is_allowed(
        [NMCLI_BIN, "--wait", "40", "device", "wifi", "connect", "Cafe", "password", "secret", "ifname", "wlan1"]
    ) is False
    assert command_is_allowed([NMCLI_BIN, "--wait", "40", "connection", "up", CAFE, "ifname", "wlan0"]) is False


def test_scan_hides_hotspot_and_marks_saved_security() -> None:
    script = Script()
    result = execute("scan", {}, script)
    names = [item["ssid"] for item in result["networks"]]
    assert names == ["Home", "Corp", "Cafe", "OpenNet", "Weird:Name"]
    by_name = {item["ssid"]: item for item in result["networks"]}
    assert by_name["Home"]["saved"] is True
    assert by_name["Home"]["in_use"] is True
    assert by_name["Home"]["security"] == "wpa-psk"
    assert by_name["Cafe"]["security"] == "sae"
    assert by_name["Cafe"]["saved"] is True
    assert by_name["Corp"]["supported"] is False
    assert by_name["OpenNet"]["security"] == "open"
    assert by_name["Weird:Name"]["security"] == "transition"
    assert "CYBERDECK" not in names
    status = result["status"]
    assert status["interface"] == "wlan1"
    assert status["ssid"] == "Home"
    assert status["signal"] == 72
    assert status["ip_address"] == "192.168.4.20"
    assert status["gateway"] == "192.168.4.1"
    assert status["dns"] == ["192.168.4.1", "1.1.1.1"]
    assert status["connectivity"] == "portal"
    assert script.calls
    assert all("wlan0" not in call for call in script.calls)
    assert all(TRAP not in part for call in script.calls for part in call)


def test_upstream_actions_do_not_name_the_hotspot_adapter() -> None:
    script = Script()
    execute("disconnect", {}, script)
    assert script.calls[0] == disconnect_command()
    assert all(call[0] == NMCLI_BIN and "wlan0" not in call for call in script.calls)

    saved = Script()
    execute("connect", {"ssid": "Cafe", "password": "", "saved": True}, saved)
    assert reconnect_command(CAFE) in saved.calls
    assert all("wlan0" not in call for call in saved.calls)

    fresh = Script()
    execute("connect", {"ssid": "Cafe", "password": "correct-horse", "saved": False}, fresh)
    assert secured_connect_command("Cafe") in fresh.calls
    assert fresh.secrets == ["correct-horse"]
    rendered = json.dumps(fresh.calls)
    assert "wlan0" not in rendered
    assert "correct-horse" not in rendered
    assert "password" not in rendered


def test_rejected_networks_do_not_run_a_connection() -> None:
    script = Script()
    try:
        execute("connect", {"ssid": "CYBERDECK", "password": "correct-horse", "saved": False}, script)
    except WifiError as exc:
        assert "hotspot" in str(exc).lower()
    else:
        raise AssertionError("hotspot SSID was accepted")
    assert script.calls == []

    blocked = Script()
    try:
        execute("connect", {"ssid": "Corp", "password": "correct-horse", "saved": False}, blocked)
    except WifiError as exc:
        assert "not supported" in str(exc)
    else:
        raise AssertionError("enterprise network was accepted")
    assert all("password" not in call for call in blocked.calls)

    opened = Script()
    try:
        execute("connect", {"ssid": "OpenNet", "password": "correct-horse", "saved": False}, opened)
    except WifiError as exc:
        assert "passphrase" in str(exc) or "password" in str(exc)
    else:
        raise AssertionError("open network accepted a passphrase")
    assert open_connect_command("OpenNet") not in opened.calls


def test_nmcli_errors_drop_the_passphrase() -> None:
    message = public_nmcli_error("Secrets were required, but not provided: correct-horse", "correct-horse")
    assert message == "NetworkManager did not accept those credentials."
    assert "correct-horse" not in message


def test_helper_response_omits_the_passphrase() -> None:
    left, right = socket.socketpair()
    left.settimeout(5)
    script = Script()

    def fail_secret(args: list[str], secret: str, timeout: float) -> str:
        assert secret not in args
        detail = "connection failed for correct-horse"
        raise WifiError(public_nmcli_error(detail, secret))

    script.submit_secret = fail_secret  # type: ignore[method-assign]
    worker = threading.Thread(target=handle_connection, args=(right, script), daemon=True)
    worker.start()
    payload = json.dumps(
        {"op": "connect", "ssid": "Cafe", "password": "correct-horse", "saved": False}
    ).encode()
    left.sendall(len(payload).to_bytes(4, "big") + payload)
    length = int.from_bytes(_read(left, 4), "big")
    body = json.loads(_read(left, length))
    worker.join(timeout=5)
    left.close()
    assert body["ok"] is False
    assert "correct-horse" not in json.dumps(body)
    assert all("wlan0" not in call for call in script.calls)


def test_helper_refuses_the_production_socket() -> None:
    assert is_production_socket("/run/pideck/wifi.sock") is True
    assert main(["--socket", "/run/pideck/wifi.sock"]) == 1


def test_wifi_routes_require_a_session_and_hide_secrets(auth_client) -> None:
    secret = "correct-horse"
    recorded: dict[str, object] = {}

    class Fake:
        def call(self, op: str, body: dict[str, object]) -> dict[str, object]:
            recorded["op"] = op
            recorded["body"] = dict(body)
            return {
                "status": {
                    "interface": "wlan1",
                    "state": "connected",
                    "ssid": "Cafe",
                    "signal": 40,
                    "ip_address": "192.168.4.20",
                    "gateway": "192.168.4.1",
                    "dns": ["192.168.4.1"],
                    "connectivity": "full",
                    "connection_name": "Cafe",
                },
                "networks": [
                    {
                        "ssid": "Cafe",
                        "signal": 40,
                        "security": "sae",
                        "security_label": "WPA3",
                        "saved": False,
                        "in_use": True,
                        "supported": True,
                    }
                ],
            }

    auth_client.app.state.wifi_backend = Fake()
    response = auth_client.post(
        "/api/wifi/connect",
        json={"ssid": "Cafe", "password": secret, "saved": False},
        headers=ORIGIN,
    )
    assert response.status_code == 200, response.text
    assert secret not in response.text
    assert recorded["body"] == {"ssid": "Cafe", "password": secret, "saved": False}
    extra = auth_client.post(
        "/api/wifi/connect",
        json={"ssid": "Cafe", "password": secret, "saved": False, "ifname": "wlan0"},
        headers=ORIGIN,
    )
    assert extra.status_code == 422


def test_anonymous_wifi_is_rejected(client) -> None:
    assert client.get("/api/wifi/status").status_code == 401
    assert client.post("/api/wifi/scan", headers=ORIGIN).status_code == 401


def test_missing_helper_reports_unavailable(auth_client, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PIDECK_WIFI_SOCKET", str(tmp_path / "missing.sock"))
    auth_client.app.state.wifi_backend = None
    response = auth_client.get("/api/wifi/status")
    assert response.status_code == 503
    assert response.json()["detail"] == "Upstream Wi-Fi control is unavailable."


def test_polkit_rule_is_limited_to_the_helper_account() -> None:
    text = Path(__file__).resolve().parents[2].joinpath("polkit", "50-pideck-wifi.rules").read_text(encoding="utf-8")
    assert 'subject.user !== "pideck-wifi"' in text
    assert "org.freedesktop.NetworkManager.enable-disable-wifi" not in text
    assert "org.freedesktop.NetworkManager.enable-disable-network" not in text
    assert "org.freedesktop.NetworkManager.settings.modify.own" not in text
    assert "org.freedesktop.NetworkManager.wifi.scan" in text
    assert "org.freedesktop.NetworkManager.network-control" in text
    assert "org.freedesktop.NetworkManager.settings.modify.system" in text
    unit = Path(__file__).resolve().parents[2].joinpath("systemd", "pideck-wifi.service").read_text(encoding="utf-8")
    socket = Path(__file__).resolve().parents[2].joinpath("systemd", "pideck-wifi.socket").read_text(encoding="utf-8")
    assert "User=pideck-wifi\n" in unit
    assert "User=root" not in unit
    assert "wlan0" not in unit
    assert "tailscale" not in unit.lower()
    assert "Wants=NetworkManager.service" not in unit
    assert "After=network.target dbus.service NetworkManager.service\n" in unit
    assert "Requires=pideck-wifi.socket\n" in unit
    assert "LoadCredential" not in unit
    assert "SocketMode=0600\n" in socket
    assert "SocketUser=pideck\n" in socket
    assert "SocketGroup=pideck\n" in socket
    assert "ListenStream=/run/pideck/wifi.sock\n" in socket


def test_passphrase_prompt_is_not_any_question() -> None:
    assert prompt_kind(b"Password (802-11-wireless-security.psk): ") == "secret"
    assert prompt_kind(b"Save this connection? ") == "other"
    assert prompt_kind(b"connecting") == ""


def test_saved_profile_for_another_adapter_is_not_activated() -> None:
    script = Script()
    script.replies[tuple(profile_property_command(CAFE, "802-11-wireless.mac-address"))] = "aa:bb:cc:dd:ee:ff\n"
    script.replies[tuple(device_show_command())] = DEVICE_SHOW + "GENERAL.HWADDR:11:22:33:44:55:66\n"
    try:
        execute("connect", {"ssid": "Cafe", "password": "", "saved": True}, script)
    except WifiError as exc:
        assert "different adapter" in str(exc)
    else:
        raise AssertionError("a profile pinned to another adapter was activated")
    assert reconnect_command(CAFE) not in script.calls


def test_saved_profile_with_a_master_is_not_activated() -> None:
    script = Script()
    script.replies[tuple(profile_property_command(CAFE, "connection.master"))] = "br0\n"
    try:
        execute("connect", {"ssid": "Cafe", "password": "", "saved": True}, script)
    except WifiError as exc:
        assert "plain upstream" in str(exc)
    else:
        raise AssertionError("a subordinate profile was activated")
    assert reconnect_command(CAFE) not in script.calls


def test_connectivity_lookup_cannot_fail_a_disconnect() -> None:
    class Flaky(Script):
        def __call__(self, args: list[str], timeout: float) -> str:
            if args == connectivity_command():
                raise WifiError("connectivity probe failed")
            return super().__call__(args, timeout)

    script = Flaky()
    result = execute("disconnect", {}, script)
    assert disconnect_command() in script.calls
    assert result["status"]["connectivity"] == "unknown"


def test_a_second_change_is_rejected_while_one_is_running() -> None:
    started = threading.Event()
    release = threading.Event()
    script = Script()

    def blocking(args: list[str], timeout: float) -> str:
        if args == disconnect_command() and not started.is_set():
            started.set()
            assert release.wait(3)
        return Script.__call__(script, args, timeout)

    worker = threading.Thread(target=lambda: handle_payload({"op": "disconnect"}, blocking), daemon=True)
    worker.start()
    assert started.wait(3)
    try:
        try:
            handle_payload({"op": "disconnect"}, script)
        except WifiError as exc:
            assert exc.status_code == 409
        else:
            raise AssertionError("overlapping Wi-Fi change was accepted")
    finally:
        release.set()
        worker.join(3)


def _read(conn: socket.socket, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        piece = conn.recv(size - len(chunks))
        if not piece:
            break
        chunks.extend(piece)
    return bytes(chunks)
