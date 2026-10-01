from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "render_unit",
    Path(__file__).resolve().parents[2] / "scripts" / "render-unit.py",
)
assert _SPEC is not None and _SPEC.loader is not None
render_unit_module = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(render_unit_module)


def test_systemd_token_escapes_spaces_without_quotes() -> None:
    token = render_unit_module.systemd_token("/home/pi/my deck")
    assert token == "/home/pi/my\\x20deck"
    assert '"' not in token
    assert token.startswith("/")


def test_render_unit_writes_absolute_unquoted_paths() -> None:
    template = Path(__file__).resolve().parents[2].joinpath("systemd", "pideck.service").read_text(encoding="utf-8")
    rendered = render_unit_module.render_unit(template, Path("/home/pi/pideck"), "pi", "pi")
    assert 'WorkingDirectory=/home/pi/pideck\n' in rendered
    assert "ExecStart=/home/pi/pideck/backend/.venv/bin/python -m backend\n" in rendered
    assert "EnvironmentFile=/home/pi/pideck/.env\n" in rendered
    assert "__APP_DIR__" not in rendered
    assert 'WorkingDirectory="' not in rendered


def test_relative_install_path_is_rejected() -> None:
    with pytest.raises(SystemExit):
        render_unit_module.absolute_directory("pideck")
