"""The device page is a card layout with separate connection sections."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PAGE = (ROOT / "frontend" / "src" / "pages" / "DevicesPage.tsx").read_text(encoding="utf-8")
CSS = (ROOT / "frontend" / "src" / "styles" / "global.css").read_text(encoding="utf-8")


def test_device_page_uses_cards_and_management_actions() -> None:
    assert "device-grid" in PAGE
    assert "device-card" in PAGE
    assert "Previously seen" in PAGE
    assert "Status unknown" in PAGE
    assert "are not marked offline" in PAGE
    assert "Rename" in PAGE
    assert "View details" in PAGE
    assert "Disconnect" in PAGE
    assert "device-alias" not in PAGE
    assert "Actions for" in PAGE
    assert "can reconnect on its own" in PAGE
    assert "does not block" in PAGE
    assert "Connected" in PAGE
    assert "Unavailable" in PAGE


def test_device_cards_have_desktop_and_mobile_layouts() -> None:
    desktop = CSS.split("@media (max-width: 720px)", 1)[0]
    mobile = CSS.split("@media (max-width: 720px)", 1)[1].split("@media", 1)[0]
    assert ".device-grid" in desktop
    assert "repeat(auto-fit, minmax(min(100%, 340px), 1fr))" in desktop
    assert ".device-card" in desktop
    assert ".device-grid" in mobile
    assert "grid-template-columns: 1fr" in mobile
    assert ".device-stats" in mobile
    assert "grid-template-columns: 1fr" in mobile.split(".device-stats", 1)[1]
