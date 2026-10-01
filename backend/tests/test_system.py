from __future__ import annotations

from fastapi.testclient import TestClient


def test_snapshot_shape(auth_client: TestClient) -> None:
    response = auth_client.get("/api/system/snapshot")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"cpu", "memory", "storage", "network", "system", "battery", "collected_at"}
    assert 0 <= body["cpu"]["usage_percent"] <= 100
    assert isinstance(body["cpu"]["per_core_percent"], list)
    assert body["cpu"]["per_core_percent"]
    assert body["memory"]["total_bytes"] > 0
    assert body["storage"]["total_bytes"] > 0
    assert body["storage"]["free_bytes"] >= 0
    assert body["network"]["hostname"]
    assert body["network"]["tailscale_status"] in {"online", "offline", "unavailable"}
    assert body["system"]["kernel"]
    assert body["system"]["uptime_seconds"] >= 0
    battery = body["battery"]
    assert isinstance(battery["available"], bool)
    if not battery["available"]:
        assert battery["message"] == "Battery information unavailable"


def test_stats_and_info_match_snapshot(auth_client: TestClient) -> None:
    snapshot = auth_client.get("/api/system/snapshot").json()
    stats = auth_client.get("/api/system/stats")
    info = auth_client.get("/api/system/info")
    assert stats.status_code == 200
    assert info.status_code == 200
    assert stats.json()["cpu"] == snapshot["cpu"]
    assert stats.json()["memory"] == snapshot["memory"]
    assert stats.json()["battery"] == snapshot["battery"]
    assert info.json() == snapshot["system"]


def test_battery_reader_never_raises() -> None:
    from backend.services.battery import UNAVAILABLE, read_battery

    info = read_battery()
    assert isinstance(info.available, bool)
    if not info.available:
        assert info.message == UNAVAILABLE


def test_collect_snapshot_directly() -> None:
    from backend.services.system_monitor import collect_snapshot

    snap = collect_snapshot()
    assert snap.memory.total_bytes > 0
    assert snap.network.hostname
