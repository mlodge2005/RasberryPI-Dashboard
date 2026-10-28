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
    assert "User=pi\n" in rendered
    assert "Group=pi\n" in rendered
    assert "WorkingDirectory=/home/pi/pideck\n" in rendered
    assert "ExecStart=/home/pi/pideck/backend/.venv/bin/python -m backend\n" in rendered
    assert "EnvironmentFile=/home/pi/pideck/.env\n" in rendered
    assert "ReadWritePaths=/home/pi/pideck/data\n" in rendered
    assert "__APP_DIR__" not in rendered
    assert 'WorkingDirectory="' not in rendered


def test_render_refuses_root() -> None:
    template = Path(__file__).resolve().parents[2].joinpath("systemd", "pideck.service").read_text(encoding="utf-8")
    with pytest.raises(SystemExit):
        render_unit_module.render_unit(template, Path("/home/admin/RasberryPI-Dashboard"), "root", "root")


def test_production_unit_is_unprivileged() -> None:
    root = Path(__file__).resolve().parents[2]
    template = (root / "systemd" / "pideck.service").read_text(encoding="utf-8")
    rendered = render_unit_module.render_unit(
        template,
        Path("/home/admin/RasberryPI-Dashboard"),
        "pideck",
        "pideck",
    )
    assert "User=pideck\n" in rendered
    assert "Group=pideck\n" in rendered
    assert "NoNewPrivileges=yes\n" in rendered
    assert "ProtectSystem=strict\n" in rendered
    assert "LoadCredential=session_secret:/etc/pideck/session.secret\n" in rendered
    assert "ReadWritePaths=/home/admin/RasberryPI-Dashboard/data\n" in rendered
    assert "User=root" not in rendered
    assert "Group=root" not in rendered


def test_terminal_unit_is_a_separate_account() -> None:
    root = Path(__file__).resolve().parents[2]
    template = (root / "systemd" / "pideck-terminal.service").read_text(encoding="utf-8")
    rendered = render_unit_module.render_unit(
        template,
        Path("/home/admin/RasberryPI-Dashboard"),
        "pideck-term",
        "pideck-term",
    )
    assert "User=pideck-term\n" in rendered
    assert "Group=pideck-term\n" in rendered
    assert "ExecStart=/home/admin/RasberryPI-Dashboard/backend/.venv/bin/python -m backend.terminal_helper\n" in rendered
    assert "NoNewPrivileges=yes\n" in rendered
    assert "LoadCredential" not in rendered
    assert "User=root" not in rendered
    with pytest.raises(SystemExit):
        render_unit_module.render_unit(template, Path("/home/admin/RasberryPI-Dashboard"), "root", "pideck-term")


def test_relative_install_path_is_rejected() -> None:
    with pytest.raises(SystemExit):
        render_unit_module.absolute_directory("pideck")
