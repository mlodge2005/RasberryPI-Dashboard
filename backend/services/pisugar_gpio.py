"""Read PiSugar S Plus external power from GPIO3 / SCL.

Official behavior, with the auto-start switch ON
(https://docs.pisugar.com/docs/product-wiki/battery/pisugar-s-series):

- external power connected -> SCL / GPIO3 is LOW
- external power absent -> SCL / GPIO3 stays HIGH

That is a pin level, not an I2C register. The same documentation says SCL is
held low while external power is present, so auto-start cannot be used together
with I2C. This module does not open an I2C bus and does not read PiSugar 2/3
battery registers.

The pin is read with `gpioget` (libgpiod). `/sys/class/gpio` is not used.
If the pin cannot be read, the result is null. Nothing here guesses.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

I2C_DETAIL = (
    "External-power detection is disabled because I2C is enabled. "
    "GPIO3 is the I2C clock. PiSugar S Plus reports external power on that pin "
    "only when the auto-start switch is on, and that mode cannot be used together with I2C."
)
GPIO_DETAIL = (
    "External power could not be read from GPIO3. Install the gpiod package so "
    "gpioget is available, and switch on PiSugar S Plus auto-start. "
    "PiDeck did not guess a power state."
)
BUSY_DETAIL = (
    "GPIO3 is in use by another program, so external power was not read. "
    "PiSugar auto-start and I2C cannot share this pin."
)

_I2C_PARAMS = {"dtparam=i2c_arm=on", "dtparam=i2c=on"}


@dataclass(frozen=True)
class PowerReading:
    external_power: bool | None
    detail: str | None = None


def read_external_power() -> PowerReading:
    if _i2c_active():
        return PowerReading(None, I2C_DETAIL)
    level, problem = _read_gpio3_level()
    if problem == "busy":
        return PowerReading(None, BUSY_DETAIL)
    if level is None:
        return PowerReading(None, GPIO_DETAIL)
    # LOW means external power is present. HIGH means it is not.
    return PowerReading(level == 0, None)


def external_power_from_level(level: int) -> bool | None:
    if level == 0:
        return True
    if level == 1:
        return False
    return None


def parse_gpioget_output(text: str) -> int | None:
    cleaned = text.strip()
    if cleaned in {"0", "1"}:
        return int(cleaned)
    lowered = cleaned.lower()
    if "inactive" in lowered:
        return 0
    if re.search(r"\bactive\b", lowered):
        return 1
    numbers = re.findall(r"\b[01]\b", cleaned)
    if len(numbers) == 1:
        return int(numbers[0])
    return None


def gpio_chip_for_model(model: str) -> str:
    if "Pi 5" in model or "Compute Module 5" in model:
        return "gpiochip4"
    return "gpiochip0"


def config_enables_i2c(text: str) -> bool:
    for line in text.splitlines():
        stripped = line.split("#", 1)[0].strip().replace(" ", "")
        if stripped in _I2C_PARAMS:
            return True
    return False


def _i2c_active() -> bool:
    if Path("/dev/i2c-1").exists() or Path("/sys/bus/i2c/devices/i2c-1").exists():
        return True
    for path in (Path("/boot/firmware/config.txt"), Path("/boot/config.txt")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if config_enables_i2c(text):
            return True
    return False


def _pi_model() -> str:
    try:
        return Path("/proc/device-tree/model").read_bytes().decode("utf-8", "replace")
    except OSError:
        return ""


def _read_gpio3_level() -> tuple[int | None, str | None]:
    binary = shutil.which("gpioget")
    if not binary:
        return None, None
    chip = gpio_chip_for_model(_pi_model())
    commands = (
        [binary, "--numeric", "-c", chip, "3"],
        [binary, chip, "3"],
    )
    saw_busy = False
    for argv in commands:
        try:
            result = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        stderr = result.stderr or ""
        if "busy" in stderr.lower() or "permission denied" in stderr.lower():
            saw_busy = "busy" in stderr.lower()
        if result.returncode != 0:
            continue
        parsed = parse_gpioget_output(result.stdout or "")
        if parsed is not None:
            return parsed, None
    return None, "busy" if saw_busy else None
