"""PiSugar S Plus estimate: GPIO polarity, sessions, and calibration."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend.tests.constants import ORIGIN

BOOT = datetime(2026, 9, 1, tzinfo=timezone.utc)


def _prepare(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PIDECK_DATABASE", str(tmp_path / "pideck.db"))
    monkeypatch.setenv("PIDECK_SESSION_SECRET", "test-session-secret-should-be-long-enough!!")
    monkeypatch.setenv("PIDECK_DEV", "true")
    monkeypatch.setenv("PIDECK_HOST", "127.0.0.1")
    from backend.config import get_settings

    get_settings.cache_clear()
    from backend.database import init_db

    init_db()


def _off(detail: str | None = None):
    from backend.services.pisugar_gpio import PowerReading

    return PowerReading(False, detail)


def _on(detail: str | None = None):
    from backend.services.pisugar_gpio import PowerReading

    return PowerReading(True, detail)


def _unknown():
    from backend.services.pisugar_gpio import PowerReading

    return PowerReading(None, "unreadable")


def _observe(reading, when: datetime, boot: datetime = BOOT):
    from backend.services.battery_estimate import observe_power

    return observe_power(reading, now=when, boot_time=boot)


def _save(runtime: float | None, charge: float | None) -> None:
    from backend.services.battery_store import save_battery_settings

    save_battery_settings(runtime, charge)


def test_level_bands_and_runtime_math() -> None:
    from backend.services.battery_math import (
        bound_runtime,
        charge_recommended_seconds,
        estimated_percent,
        level_for,
        remaining_seconds,
        rolling_median,
    )

    assert level_for(40.1) == "normal"
    assert level_for(40) == "low"
    assert level_for(20) == "low"
    assert level_for(19.9) == "charge_soon"
    assert level_for(10) == "charge_soon"
    assert level_for(9.9) == "critical"
    assert estimated_percent(100, 10 * 3600, 300) == 0
    assert estimated_percent(100, 0, 300) == 100
    assert remaining_seconds(300, 62) == pytest.approx(11160)
    assert charge_recommended_seconds(300, 62) == pytest.approx(7560)
    assert rolling_median([291, 307, 298]) == 298
    assert rolling_median([291, 307, 298, 900]) == pytest.approx(302.5)
    assert bound_runtime(300, 30) == pytest.approx(225)
    assert bound_runtime(None, 30) == 30


def test_gpio_polarity_and_i2c_conflict(monkeypatch) -> None:
    from backend.services import pisugar_gpio

    assert pisugar_gpio.external_power_from_level(0) is True
    assert pisugar_gpio.external_power_from_level(1) is False
    assert pisugar_gpio.parse_gpioget_output("0\n") == 0
    assert pisugar_gpio.parse_gpioget_output("1") == 1
    assert pisugar_gpio.parse_gpioget_output('"3"=inactive') == 0
    assert pisugar_gpio.parse_gpioget_output('"3"=active') == 1
    assert pisugar_gpio.gpio_chip_for_model("Raspberry Pi 3 Model B Plus Rev 1.3") == "gpiochip0"
    assert pisugar_gpio.gpio_chip_for_model("Raspberry Pi 5 Model B") == "gpiochip4"
    assert pisugar_gpio.config_enables_i2c("dtparam=i2c_arm=on\n") is True
    assert pisugar_gpio.config_enables_i2c("# dtparam=i2c_arm=on\n") is False

    calls = {"count": 0}

    def fail_if_called() -> tuple[int | None, str | None]:
        calls["count"] += 1
        return 0, None

    monkeypatch.setattr(pisugar_gpio, "_i2c_active", lambda: True)
    monkeypatch.setattr(pisugar_gpio, "_read_gpio3_level", fail_if_called)
    blocked = pisugar_gpio.read_external_power()
    assert blocked.external_power is None
    assert calls["count"] == 0

    monkeypatch.setattr(pisugar_gpio, "_i2c_active", lambda: False)
    monkeypatch.setattr(pisugar_gpio, "_read_gpio3_level", lambda: (0, None))
    assert pisugar_gpio.read_external_power().external_power is True
    monkeypatch.setattr(pisugar_gpio, "_read_gpio3_level", lambda: (1, None))
    assert pisugar_gpio.read_external_power().external_power is False
    monkeypatch.setattr(pisugar_gpio, "_read_gpio3_level", lambda: (None, None))
    assert pisugar_gpio.read_external_power().external_power is None


def test_short_charge_keeps_the_previous_estimate(tmp_path, monkeypatch) -> None:
    _prepare(tmp_path, monkeypatch)
    _save(300, 120)
    start = BOOT + timedelta(hours=1)
    _observe(_off(), start)
    later = start + timedelta(minutes=150)
    running = _observe(_off(), later)
    assert running.estimated_percent == pytest.approx(50)
    assert running.level == "normal"
    assert running.power_label == "Running on Battery"

    _observe(_on(), later)
    plugged = _observe(_on(), later + timedelta(minutes=10))
    assert plugged.power_label == "Charging"
    assert plugged.estimated_percent is None
    assert plugged.estimate_uncertain is True
    assert plugged.advice == "Charge estimate uncertain"

    resumed = _observe(_off(), later + timedelta(minutes=10))
    assert resumed.estimated_percent == pytest.approx(50)
    assert resumed.estimate_uncertain is True
    assert resumed.estimated_remaining_seconds is None
    assert resumed.advice == "Charge estimate uncertain"


def test_full_charge_time_resets_to_100(tmp_path, monkeypatch) -> None:
    _prepare(tmp_path, monkeypatch)
    _save(300, 120)
    start = BOOT + timedelta(hours=1)
    _observe(_off(), start)
    _observe(_on(), start)
    charged = _observe(_on(), start + timedelta(minutes=120))
    assert charged.estimated_percent == pytest.approx(100)
    assert charged.estimate_uncertain is False
    assert charged.advice == "Full charge assumed"

    unplugged = _observe(_off(), start + timedelta(minutes=120))
    assert unplugged.estimated_percent == pytest.approx(100)
    assert unplugged.estimate_uncertain is False
    assert unplugged.charge_recommended_in_seconds == pytest.approx(240 * 60)


def test_brief_plug_in_with_no_history_does_not_assume_full(tmp_path, monkeypatch) -> None:
    _prepare(tmp_path, monkeypatch)
    _save(300, 180)
    start = BOOT + timedelta(hours=1)
    _observe(_on(), start)
    unplugged = _observe(_off(), start + timedelta(minutes=10))
    assert unplugged.estimated_percent is None
    assert unplugged.estimate_uncertain is True


def test_backend_restart_keeps_the_open_battery_session(tmp_path, monkeypatch) -> None:
    _prepare(tmp_path, monkeypatch)
    _save(300, 120)
    from backend.services.battery_store import current_open_session_id

    start = BOOT + timedelta(hours=1)
    _observe(_off(), start)
    first = current_open_session_id()
    later = _observe(_off(), start + timedelta(minutes=114))
    assert current_open_session_id() == first
    assert later.estimated_percent == pytest.approx(62)
    assert later.level == "normal"
    assert later.estimated_remaining_seconds == pytest.approx(186 * 60)
    assert later.charge_recommended_in_seconds == pytest.approx(126 * 60)
    assert later.time_on_battery_seconds == pytest.approx(114 * 60)
    assert "not a battery percentage" in later.disclaimer


def test_power_loss_after_a_known_full_charge_updates_the_median(tmp_path, monkeypatch) -> None:
    _prepare(tmp_path, monkeypatch)
    _save(300, 120)
    start = BOOT + timedelta(hours=1)
    _observe(_off(), start)
    _observe(_on(), start)
    full = start + timedelta(minutes=120)
    _observe(_off(), full)
    end = full + timedelta(hours=4)
    _observe(_off(), end)
    reboot = end + timedelta(minutes=3)
    view = _observe(_on(), reboot + timedelta(minutes=1), boot=reboot)
    assert view.stats.calibration_sample_count == 1
    assert view.stats.calibration_samples_minutes[0] == pytest.approx(240)
    assert view.stats.estimated_full_runtime_minutes == pytest.approx(240)
    assert view.power_label == "Charging"


def test_assumed_full_run_is_not_treated_as_calibration(tmp_path, monkeypatch) -> None:
    _prepare(tmp_path, monkeypatch)
    _save(300, 120)
    start = BOOT + timedelta(hours=1)
    _observe(_off(), start)
    end = start + timedelta(hours=5)
    _observe(_off(), end)
    reboot = end + timedelta(minutes=3)
    view = _observe(_on(), reboot + timedelta(minutes=1), boot=reboot)
    assert view.stats.calibration_sample_count == 0
    assert view.stats.estimated_full_runtime_minutes == pytest.approx(300)


def test_unknown_gpio_does_not_invent_a_session(tmp_path, monkeypatch) -> None:
    _prepare(tmp_path, monkeypatch)
    view = _observe(_unknown(), BOOT + timedelta(hours=1))
    assert view.external_power is None
    assert view.tracking is False
    from backend.services.battery_store import current_open_session_id

    assert current_open_session_id() is None


def test_battery_settings_require_login(client: TestClient) -> None:
    assert client.get("/api/settings/battery").status_code == 401


def test_battery_settings_roundtrip(auth_client: TestClient) -> None:
    empty = auth_client.get("/api/settings/battery")
    assert empty.status_code == 200
    assert empty.json()["estimated_full_runtime_minutes"] is None
    assert empty.json()["full_charge_time_minutes"] is None

    saved = auth_client.put(
        "/api/settings/battery",
        json={"estimated_full_runtime_minutes": 300, "full_charge_time_minutes": 180},
        headers=ORIGIN,
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["estimated_full_runtime_minutes"] == 300
    assert saved.json()["full_charge_time_minutes"] == 180

    rejected = auth_client.put(
        "/api/settings/battery",
        json={"estimated_full_runtime_minutes": 1, "full_charge_time_minutes": None},
        headers=ORIGIN,
    )
    assert rejected.status_code == 400
