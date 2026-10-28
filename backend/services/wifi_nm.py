"""Plan and interpret NetworkManager commands for the upstream adapter.

The hotspot on wlan0 is unmanaged by NetworkManager and is never named here.
Every command is an argument list for /usr/bin/nmcli. Nothing is passed through
a shell, and the interface name is not taken from the request.
"""

from __future__ import annotations

import re
from typing import Callable

UPSTREAM_IFACE = "wlan1"
HOTSPOT_SSID = "CYBERDECK"
NMCLI_BIN = "/usr/bin/nmcli"
_UUID = re.compile(r"^[0-9a-fA-F-]{1,64}$")
_IPV4 = re.compile(r"^(?:\d{1,3}\.){3}\d{1,3}$")
_HEX64 = re.compile(r"^[0-9a-fA-F]{64}$")

Runner = Callable[[list[str], float], str]


class WifiError(Exception):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def split_terse(line: str) -> list[str]:
    fields: list[str] = []
    current: list[str] = []
    escaped = False
    for char in line.rstrip("\r\n"):
        if escaped:
            current.append(char)
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == ":":
            fields.append("".join(current))
            current = []
            continue
        current.append(char)
    if escaped:
        current.append("\\")
    fields.append("".join(current))
    return fields


def validate_ssid(ssid: str) -> str:
    if not isinstance(ssid, str) or ssid == "":
        raise WifiError("Enter a network name.")
    if any(ord(char) < 32 or ord(char) == 127 for char in ssid):
        raise WifiError("That network name is not valid.")
    if len(ssid.encode("utf-8")) > 32:
        raise WifiError("That network name is too long.")
    if ssid == HOTSPOT_SSID:
        raise WifiError("The CYBERDECK hotspot is not an upstream network.")
    return ssid


def validate_password(password: str) -> str:
    if not isinstance(password, str):
        raise WifiError("That password is not valid.")
    if any(ord(char) < 32 or ord(char) == 127 for char in password):
        raise WifiError("That password is not valid.")
    if _HEX64.fullmatch(password):
        return password
    if len(password) < 8 or len(password) > 63:
        raise WifiError("A Wi-Fi password must be 8 to 63 characters.")
    return password


def classify_security(raw: str) -> tuple[str, str, bool]:
    text = " ".join(raw.replace("_", " ").split())
    folded = text.upper()
    if not folded or folded in {"--", "NONE", "OPEN", "OWE", "ENHANCED OPEN"}:
        label = "Enhanced open" if "OWE" in folded or "ENHANCED" in folded else "Open"
        return "open", label, True
    if "802.1X" in folded or "ENTERPRISE" in folded or "WEP" in folded:
        return "unsupported", text or "Unsupported", False
    has_wpa3 = "WPA3" in folded or "SAE" in folded
    has_wpa2 = "WPA2" in folded or "WPA1" in folded or folded == "WPA"
    if has_wpa3 and has_wpa2:
        return "transition", "WPA2/WPA3", True
    if has_wpa3:
        return "sae", "WPA3", True
    if "WPA2" in folded or "RSN" in folded:
        return "wpa-psk", "WPA2", True
    if folded in {"WPA", "WPA1"}:
        return "unsupported", "WPA", False
    return "unsupported", text or "Unsupported", False


_PROFILE_FIELDS = frozenset(
    {
        "802-11-wireless.ssid",
        "connection.interface-name",
        "802-11-wireless.mac-address",
        "connection.master",
        "connection.slave-type",
        "connection.secondaries",
    }
)
_FORBIDDEN_TOKENS = frozenset(
    {
        "password",
        "psk",
        "radio",
        "networking",
        "wlan0",
        "hostname",
        "checkpoint",
        "reload",
        "delete",
        "modify",
    }
)


def _ssid_token(ssid: str) -> str:
    cleaned = validate_ssid(ssid)
    if cleaned.startswith("-"):
        raise WifiError("That network name is not valid.")
    return cleaned


def command_is_allowed(command: list[str]) -> bool:
    """True only for the fixed nmcli forms the helper may run.

    A passphrase is never an argument. Nothing in the list can name wlan0,
    turn radios off, or disable networking.
    """
    if not command or command[0] != NMCLI_BIN:
        return False
    if any(not isinstance(part, str) or any(char in part for char in "\n\r\x00") for part in command):
        return False
    if any(part in _FORBIDDEN_TOKENS for part in command):
        return False
    rest = command[1:]
    if rest == ["-t", "-f", "NAME,UUID,TYPE,DEVICE", "connection", "show"]:
        return True
    if rest == ["-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device", "status"]:
        return True
    if rest == ["-t", "device", "show", UPSTREAM_IFACE]:
        return True
    if rest == ["-g", "CONNECTIVITY", "general"]:
        return True
    if rest[:7] == ["-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY", "device", "wifi", "list", "ifname"]:
        if len(rest) == 8 and rest[7] == UPSTREAM_IFACE:
            return True
        return len(rest) == 10 and rest[7:] == [UPSTREAM_IFACE, "--rescan", "yes"]
    if rest == ["device", "disconnect", UPSTREAM_IFACE]:
        return True
    if rest[:5] == ["--wait", "40", "device", "wifi", "connect"] and len(rest) == 8:
        return rest[6:] == ["ifname", UPSTREAM_IFACE] and _ssid_is_token(rest[5])
    if rest[:6] == ["--ask", "--wait", "40", "device", "wifi", "connect"] and len(rest) == 9:
        return rest[7:] == ["ifname", UPSTREAM_IFACE] and _ssid_is_token(rest[6])
    if rest[:4] == ["--wait", "40", "connection", "up"] and len(rest) == 7:
        return rest[5:] == ["ifname", UPSTREAM_IFACE] and _UUID.fullmatch(rest[4]) is not None
    if len(rest) == 5 and rest[0] == "-g" and rest[1] in _PROFILE_FIELDS and rest[2:4] == ["connection", "show"]:
        return _UUID.fullmatch(rest[4]) is not None
    return False


def _ssid_is_token(ssid: str) -> bool:
    try:
        _ssid_token(ssid)
    except WifiError:
        return False
    return True


def _nm(*args: str) -> list[str]:
    command = [NMCLI_BIN, *args]
    if not command_is_allowed(command):
        raise WifiError("Refusing a Wi-Fi command that is not limited to the upstream adapter.")
    return command


def connection_list_command() -> list[str]:
    return _nm("-t", "-f", "NAME,UUID,TYPE,DEVICE", "connection", "show")


def profile_property_command(uuid: str, prop: str) -> list[str]:
    if prop not in _PROFILE_FIELDS or _UUID.fullmatch(uuid) is None:
        raise WifiError("That saved profile is not valid.")
    return _nm("-g", prop, "connection", "show", uuid)


def profile_ssid_command(uuid: str) -> list[str]:
    return profile_property_command(uuid, "802-11-wireless.ssid")


def profile_interface_command(uuid: str) -> list[str]:
    return profile_property_command(uuid, "connection.interface-name")


def device_status_command() -> list[str]:
    return _nm("-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device", "status")


def device_show_command() -> list[str]:
    return _nm("-t", "device", "show", UPSTREAM_IFACE)


def connectivity_command() -> list[str]:
    return _nm("-g", "CONNECTIVITY", "general")


def wifi_list_command(*, rescan: bool) -> list[str]:
    command = ["-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY", "device", "wifi", "list", "ifname", UPSTREAM_IFACE]
    if rescan:
        command.extend(["--rescan", "yes"])
    return _nm(*command)


def disconnect_command() -> list[str]:
    return _nm("device", "disconnect", UPSTREAM_IFACE)


def open_connect_command(ssid: str) -> list[str]:
    return _nm("--wait", "40", "device", "wifi", "connect", _ssid_token(ssid), "ifname", UPSTREAM_IFACE)


def secured_connect_command(ssid: str) -> list[str]:
    """Ask NetworkManager for the passphrase on the terminal, not on the command line."""
    return _nm("--ask", "--wait", "40", "device", "wifi", "connect", _ssid_token(ssid), "ifname", UPSTREAM_IFACE)


def reconnect_command(uuid: str) -> list[str]:
    if _UUID.fullmatch(uuid) is None:
        raise WifiError("That saved profile is not valid.")
    return _nm("--wait", "40", "connection", "up", uuid, "ifname", UPSTREAM_IFACE)


def public_nmcli_error(detail: str, password: str = "") -> str:
    cleaned = detail.replace("\r", " ").replace("\n", " ").strip()
    if password:
        cleaned = cleaned.replace(password, "")
    folded = cleaned.lower()
    if "no network with ssid" in folded or "not found" in folded:
        return "That network is no longer visible on the upstream adapter."
    if "secret" in folded or "password" in folded or "802-11-wireless-security" in folded:
        return "NetworkManager did not accept those credentials."
    if "not authorized" in folded or "insufficient privileges" in folded or "permission" in folded:
        return "The Wi-Fi helper is not allowed to control the upstream adapter."
    if "timeout" in folded or "timed out" in folded:
        return "The Wi-Fi operation timed out."
    if "not available" in folded or "unavailable" in folded:
        return "The upstream adapter is unavailable."
    if not cleaned:
        return "NetworkManager refused the request."
    return cleaned[:180]


def scrub(value: object, secret: str) -> object:
    if not secret:
        return value
    if isinstance(value, str):
        return value.replace(secret, "")
    if isinstance(value, list):
        return [scrub(item, secret) for item in value]
    if isinstance(value, dict):
        return {str(key): scrub(item, secret) for key, item in value.items()}
    return value


def _ipv4(value: str) -> str | None:
    candidate = value.strip().split("/", 1)[0]
    if _IPV4.fullmatch(candidate) is None:
        return None
    octets = [int(part) for part in candidate.split(".")]
    if any(part > 255 for part in octets):
        return None
    return candidate


def _parse_show(text: str) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.split("[", 1)[0].strip().upper()
        found.setdefault(key, []).append(value.strip())
    return found


def _parse_state(word: str) -> str:
    folded = word.strip().lower()
    known = {
        "connected",
        "connecting",
        "disconnected",
        "disconnecting",
        "unavailable",
        "unmanaged",
        "failed",
        "deactivating",
        "prepare",
        "config",
        "ip-config",
        "ip-check",
        "secondaries",
        "activated",
        "need-auth",
    }
    if folded in known:
        return "connected" if folded == "activated" else folded
    return "unknown"


def _parse_connectivity(word: str) -> str:
    folded = word.strip().lower()
    if folded in {"full", "limited", "portal", "none", "unknown"}:
        return folded
    return "unknown"


def load_profiles(run: Runner) -> list[dict[str, str]]:
    text = run(connection_list_command(), 15)
    profiles: list[dict[str, str]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        fields = split_terse(line)
        if len(fields) < 4:
            continue
        name, uuid, kind, device = fields[0], fields[1], fields[2], fields[3]
        if kind != "802-11-wireless" or _UUID.fullmatch(uuid) is None:
            continue
        if device not in {"", UPSTREAM_IFACE}:
            continue
        ssid = run(profile_ssid_command(uuid), 10).strip()
        interface = run(profile_interface_command(uuid), 10).strip()
        if not ssid or ssid == HOTSPOT_SSID:
            continue
        if interface not in {"", UPSTREAM_IFACE}:
            continue
        try:
            validate_ssid(ssid)
        except WifiError:
            continue
        profiles.append(
            {
                "name": name,
                "uuid": uuid,
                "ssid": ssid,
                "interface": interface,
                "device": device,
            }
        )
    return profiles


def _profile_for(profiles: list[dict[str, str]], ssid: str) -> dict[str, str] | None:
    matches = [profile for profile in profiles if profile["ssid"] == ssid]
    for profile in matches:
        if profile["interface"] == UPSTREAM_IFACE:
            return profile
    return matches[0] if matches else None


def parse_networks(text: str, saved_ssids: set[str]) -> list[dict[str, object]]:
    best: dict[str, dict[str, object]] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        fields = split_terse(line)
        if len(fields) < 4:
            continue
        in_use, ssid, signal_text, security = fields[0], fields[1], fields[2], fields[3]
        if ssid == "" or ssid == HOTSPOT_SSID:
            continue
        try:
            validate_ssid(ssid)
        except WifiError:
            continue
        try:
            signal = max(0, min(100, int(signal_text)))
        except ValueError:
            signal = 0
        kind, label, supported = classify_security(security)
        row: dict[str, object] = {
            "ssid": ssid,
            "signal": signal,
            "security": kind,
            "security_label": label,
            "saved": ssid in saved_ssids,
            "in_use": in_use.strip() == "*",
            "supported": supported,
        }
        current = best.get(ssid)
        if current is None or signal > int(current["signal"]) or (signal == current["signal"] and row["in_use"]):
            if current is not None and current["in_use"]:
                row["in_use"] = True
            best[ssid] = row
    rows = list(best.values())
    rows.sort(key=lambda item: (-int(item["signal"]), str(item["ssid"]).lower()))
    return rows


def parse_status(
    device_status: str,
    device_show: str,
    wifi_list: str,
    connectivity: str,
    profiles: list[dict[str, str]],
) -> dict[str, object]:
    state = "unavailable"
    connection_name = ""
    active_ssid = ""
    for line in device_status.splitlines():
        fields = split_terse(line)
        if len(fields) < 4 or fields[0] != UPSTREAM_IFACE:
            continue
        state = _parse_state(fields[2])
        connection_name = fields[3]
        break
    show = _parse_show(device_show)
    addresses = [_ipv4(value) for value in show.get("IP4.ADDRESS", [])]
    ip_address = next((value for value in addresses if value), None)
    gateway = _ipv4(show.get("IP4.GATEWAY", [""])[0]) if show.get("IP4.GATEWAY") else None
    dns: list[str] = []
    for value in show.get("IP4.DNS", []):
        parsed = _ipv4(value)
        if parsed and parsed not in dns:
            dns.append(parsed)
    saved = {profile["ssid"] for profile in profiles}
    networks = parse_networks(wifi_list, saved)
    for network in networks:
        if network["in_use"]:
            active_ssid = str(network["ssid"])
            break
    if not active_ssid and connection_name:
        for profile in profiles:
            if profile["name"] == connection_name or profile["ssid"] == connection_name:
                active_ssid = profile["ssid"]
                break
    signal = None
    for network in networks:
        if network["ssid"] == active_ssid:
            signal = int(network["signal"])
            break
    link = _parse_connectivity(connectivity)
    connected = state == "connected"
    if not connected:
        ip_address = None
        gateway = None
        dns = []
        link = "none"
        if state == "disconnected":
            active_ssid = ""
    return {
        "interface": UPSTREAM_IFACE,
        "state": state,
        "ssid": active_ssid,
        "signal": signal,
        "ip_address": ip_address,
        "gateway": gateway,
        "dns": dns,
        "connectivity": link,
        "connection_name": connection_name if connected else "",
    }


def _optional(run: Runner, command: list[str], timeout: float) -> str:
    try:
        return run(command, timeout)
    except WifiError:
        return ""


def _blank(value: str) -> bool:
    return value.strip() in {"", "--"}


def _hwaddr(show: str) -> str:
    for line in show.splitlines():
        if line.upper().startswith("GENERAL.HWADDR:"):
            return line.split(":", 1)[1].strip().lower()
    return ""


def _guard_saved_profile(run: Runner, profile: dict[str, str]) -> None:
    uuid = profile["uuid"]
    mac = run(profile_property_command(uuid, "802-11-wireless.mac-address"), 10)
    master = run(profile_property_command(uuid, "connection.master"), 10)
    slave = run(profile_property_command(uuid, "connection.slave-type"), 10)
    secondaries = run(profile_property_command(uuid, "connection.secondaries"), 10)
    if not _blank(master) or not _blank(slave) or not _blank(secondaries):
        raise WifiError("That saved profile is not a plain upstream network.")
    if _blank(mac):
        return
    hardware = _hwaddr(run(device_show_command(), 15))
    if not hardware or mac.strip().lower() != hardware:
        raise WifiError("That saved profile is bound to a different adapter.")


def _status(run: Runner, profiles: list[dict[str, str]] | None = None) -> dict[str, object]:
    found = profiles if profiles is not None else load_profiles(run)
    return parse_status(
        run(device_status_command(), 15),
        run(device_show_command(), 15),
        run(wifi_list_command(rescan=False), 20),
        _optional(run, connectivity_command(), 10),
        found,
    )


def _visible_security(networks: list[dict[str, object]], ssid: str) -> dict[str, object] | None:
    for network in networks:
        if network["ssid"] == ssid:
            return network
    return None


def execute(op: str, body: dict[str, object], run: Runner) -> dict[str, object]:
    if op == "status":
        return {"status": _status(run), "networks": []}
    if op == "scan":
        profiles = load_profiles(run)
        networks = parse_networks(run(wifi_list_command(rescan=True), 25), {item["ssid"] for item in profiles})
        return {"status": _status(run, profiles), "networks": networks}
    if op == "disconnect":
        run(disconnect_command(), 20)
        return {"status": _status(run), "networks": []}
    if op != "connect":
        raise WifiError("That Wi-Fi operation is not available.")
    ssid_value = body.get("ssid")
    if not isinstance(ssid_value, str):
        raise WifiError("Enter a network name.")
    ssid = validate_ssid(ssid_value)
    saved_value = body.get("saved", False)
    if not isinstance(saved_value, bool):
        raise WifiError("That Wi-Fi operation is not available.")
    saved = saved_value
    password = body.get("password", "")
    if not isinstance(password, str):
        raise WifiError("That password is not valid.")
    if saved and password:
        raise WifiError("Leave the password empty to use the saved profile.")
    profiles = load_profiles(run)
    profile = _profile_for(profiles, ssid)
    if saved:
        if profile is None:
            raise WifiError("There is no saved upstream profile for that network.")
        _guard_saved_profile(run, profile)
        run(reconnect_command(profile["uuid"]), 45)
        return {"status": _status(run), "networks": []}
    networks = parse_networks(run(wifi_list_command(rescan=True), 25), {item["ssid"] for item in profiles})
    visible = _visible_security(networks, ssid)
    if visible is None:
        raise WifiError("That network is not visible on the upstream adapter.")
    if not visible["supported"]:
        raise WifiError("That security type is not supported.")
    if visible["security"] == "open":
        if password:
            raise WifiError("That network does not take a password.")
        run(open_connect_command(ssid), 45)
    else:
        if not password:
            raise WifiError("That network needs a password.")
        secret = validate_password(password)
        command = secured_connect_command(ssid)
        submit = getattr(run, "submit_secret", None)
        if not callable(submit):
            raise WifiError("Upstream Wi-Fi control is unavailable.", status_code=503)
        if any(secret in part for part in command):
            raise WifiError("Refusing to put a Wi-Fi passphrase on the command line.")
        submit(command, secret, 45)
    return {"status": scrub(_status(run), password), "networks": []}
