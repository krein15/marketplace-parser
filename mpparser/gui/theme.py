"""Colours and fonts. Tuples are (light mode, dark mode)."""

from __future__ import annotations

import sys
from pathlib import Path

import customtkinter as ctk

APP_BG = ("#F3F4F8", "#12141C")
CARD_BG = ("#FFFFFF", "#1C1F2B")
CARD_BORDER = ("#E4E6EE", "#2A2E3D")
INPUT_BG = ("#F7F8FB", "#232736")
TEXT = ("#1F2233", "#E8EAF2")
TEXT_MUTED = ("#6B7085", "#9095A8")
ACCENT = ("#4F46E5", "#6366F1")
ACCENT_HOVER = ("#4338CA", "#5458E8")
DANGER = ("#DC2626", "#EF4444")
DANGER_HOVER = ("#B91C1C", "#DC2626")
SUCCESS = ("#15803D", "#22C55E")
WARNING = ("#B45309", "#F59E0B")
NEUTRAL_BUTTON = ("#E8EAF2", "#2A2E3D")
NEUTRAL_BUTTON_HOVER = ("#DADDE8", "#343949")

MARKETPLACE_COLORS = {"wb": ("#A20D8A", "#CB11AB"), "ozon": ("#005BFF", "#2B7BFF")}

FONT_FAMILY = "Segoe UI"


def font(size: int = 13, weight: str = "normal") -> ctk.CTkFont:
    return ctk.CTkFont(family=FONT_FAMILY, size=size, weight=weight)


def resource_path(relative: str) -> Path:
    """Path to a bundled resource, both from sources and from a PyInstaller build."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    return base / relative
