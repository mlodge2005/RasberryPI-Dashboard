"""Collect one system snapshot. Individual probes fail soft."""

from __future__ import annotations

import logging
import os
import platform
import socket
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import psutil

from backend.models.schemas import (
    CpuStats,
    MemoryStats,
    NetworkStats,
    StorageStats,
    SystemInfo,
    SystemSnapshot,
)
from backend.services.battery import read_battery
from backend.services.tailscale import get_tailscale_info

logger = logging.getLogger(__name__)

_SKIP_PREFIXES = (
    "lo",
    "docker",
    "veth",
    "br-",
    "virbr",
    "zt",
    "bluetooth",
    "isatap",
    "loopback",
)


class SystemMonitor:
    """Background sampler so HTTP requests do not block on CPU measurement."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._latest: SystemSnapshot | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="pideck-monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None

    def snapshot(self) -> SystemSnapshot:
        with self._lock:
            if self._latest is not None:
                return self._latest
        snap = collect_snapshot()
        with self._lock:
            if self._latest is None:
                self._latest = snap
            return self._latest

    def _run(self) -> None:
        _prime_cpu()
        while not self._stop.is_set():
            try:
                snap = collect_snapshot()
            except Exception:
                logger.exception("System snapshot failed")
            else:
                with self._lock:
                    self._latest = snap
            if self._stop.wait(2):
                break


def collect_snapshot() -> SystemSnapshot:
    tailscale = get_tailscale_info()
    sent, received = _net_bytes()
    return SystemSnapshot(
        cpu=_cpu(),
        memory=_memory(),
        storage=_storage(),
        network=NetworkStats(
            hostname=_hostname(),
            local_ip=_local_ip(),
            tailscale_ip=tailscale.ip,
            tailscale_hostname=tailscale.hostname,
            tailscale_status=_tailscale_status(tailscale.status),
            bytes_sent=sent,
            bytes_received=received,
        ),
        system=_system_info(),
        battery=read_battery(),
        collected_at=datetime.now(timezone.utc).isoformat(),
    )


def _tailscale_status(value: str) -> Literal["online", "offline", "unavailable"]:
    if value == "online":
        return "online"
    if value == "offline":
        return "offline"
    return "unavailable"


def _prime_cpu() -> None:
    try:
        psutil.cpu_percent(interval=None, percpu=True)
        psutil.cpu_percent(interval=0.1, percpu=True)
    except Exception:
        logger.info("CPU priming failed", exc_info=True)


def _cpu() -> CpuStats:
    per_core: list[float] = []
    try:
        raw = psutil.cpu_percent(interval=None, percpu=True)
        per_core = [round(float(value), 1) for value in raw]
    except Exception:
        logger.info("CPU percent unavailable", exc_info=True)
    usage = round(sum(per_core) / len(per_core), 1) if per_core else 0.0
    return CpuStats(
        usage_percent=usage,
        per_core_percent=per_core,
        temperature_c=_temperature(),
        frequency_mhz=_frequency(),
        load_average=_load(),
    )


def _memory() -> MemoryStats:
    memory = psutil.virtual_memory()
    total = int(memory.total)
    used = int(max(0, total - int(memory.available)))
    percent = round(float(memory.percent), 1)
    return MemoryStats(used_bytes=used, total_bytes=total, percent=percent)


def _storage() -> StorageStats:
    mount = _disk_mount()
    usage = psutil.disk_usage(mount)
    total = int(usage.total)
    used = int(usage.used)
    free = int(usage.free)
    return StorageStats(
        used_bytes=used,
        free_bytes=free,
        total_bytes=total,
        percent=round(float(usage.percent), 1),
        mount=mount,
    )


def _disk_mount() -> str:
    if os.name == "nt":
        return os.environ.get("SystemDrive", "C:") + "\\"
    return "/"


def _net_bytes() -> tuple[int, int]:
    try:
        counters = psutil.net_io_counters()
    except Exception:
        logger.info("Network counters unavailable", exc_info=True)
        return 0, 0
    if counters is None:
        return 0, 0
    return int(counters.bytes_sent), int(counters.bytes_recv)


def _hostname() -> str:
    try:
        return socket.gethostname() or "localhost"
    except OSError:
        return "localhost"


def _local_ip() -> str | None:
    preferred = _outbound_ip()
    if preferred is not None and not _skip_ip(preferred):
        return preferred
    found: list[str] = []
    try:
        interfaces = psutil.net_if_addrs()
    except Exception:
        logger.info("Interface list unavailable", exc_info=True)
        return None
    for name, addresses in interfaces.items():
        if _skip_interface(name):
            continue
        for address in addresses:
            if address.family != socket.AF_INET:
                continue
            ip = address.address
            if _skip_ip(ip):
                continue
            found.append(ip)
    return found[0] if found else None


def _outbound_ip() -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("192.0.2.1", 80))
            ip = sock.getsockname()[0]
    except OSError:
        return None
    if not ip or ip.startswith("127."):
        return None
    return ip


def _skip_ip(ip: str) -> bool:
    if ip.startswith("127.") or ip.startswith("169.254."):
        return True
    parts = ip.split(".")
    if len(parts) != 4:
        return False
    try:
        first = int(parts[0])
        second = int(parts[1])
    except ValueError:
        return False
    # Tailscale and other CGNAT addresses are reported separately.
    return first == 100 and 64 <= second <= 127


def _skip_interface(name: str) -> bool:
    lowered = name.lower()
    if "tailscale" in lowered:
        return True
    virtual_markers = (
        "vbox",
        "vmware",
        "virtualbox",
        "vethernet",
        "hyper-v",
        "wsl",
        "bluetooth",
        "hamachi",
        "zerotier",
        "docker",
        "loopback",
    )
    if any(marker in lowered for marker in virtual_markers):
        return True
    return lowered.startswith(_SKIP_PREFIXES)


def _temperature() -> float | None:
    zone = Path("/sys/class/thermal/thermal_zone0/temp")
    if zone.is_file():
        try:
            raw = int(zone.read_text(encoding="utf-8", errors="replace").strip())
        except (OSError, ValueError):
            raw = None
        if raw is not None:
            celsius = raw / 1000 if raw > 1000 else float(raw)
            return round(celsius, 1)
    try:
        sensors = psutil.sensors_temperatures()
    except (AttributeError, OSError):
        return None
    if not sensors:
        return None
    preferred = ("cpu_thermal", "cpu-thermal", "coretemp", "k10temp", "soc_thermal")
    for key in preferred:
        entries = sensors.get(key) or []
        if entries and entries[0].current:
            return round(float(entries[0].current), 1)
    for entries in sensors.values():
        if entries and entries[0].current:
            return round(float(entries[0].current), 1)
    return None


def _frequency() -> float | None:
    try:
        freq = psutil.cpu_freq()
    except Exception:
        return None
    if freq is None or not freq.current:
        return None
    return round(float(freq.current), 0)


def _load() -> list[float] | None:
    try:
        load = os.getloadavg()
    except (AttributeError, OSError):
        return None
    return [round(float(value), 2) for value in load]


def _system_info() -> SystemInfo:
    boot = psutil.boot_time()
    return SystemInfo(
        os_name=_os_name(),
        kernel=platform.release(),
        architecture=platform.machine() or platform.architecture()[0],
        uptime_seconds=round(max(0.0, time.time() - boot), 1),
        boot_time=datetime.fromtimestamp(boot, timezone.utc).isoformat(),
        pi_model=_pi_model(),
    )


def _os_name() -> str:
    path = Path("/etc/os-release")
    if path.is_file():
        try:
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("PRETTY_NAME="):
                    return line.split("=", 1)[1].strip().strip('"') or platform.platform()
        except OSError:
            pass
    return platform.platform()


def _pi_model() -> str | None:
    for candidate in ("/proc/device-tree/model", "/sys/firmware/devicetree/base/model"):
        path = Path(candidate)
        if not path.is_file():
            continue
        try:
            raw = path.read_bytes().replace(b"\x00", b"").decode("utf-8", errors="replace").strip()
        except OSError:
            continue
        if raw:
            return raw
    return None
