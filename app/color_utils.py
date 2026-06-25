from __future__ import annotations

import re

from PIL import ImageColor


COLOR_ALIASES = {
    "black": "#111827",
    "blue": "#2563eb",
    "brown": "#92400e",
    "cyan": "#0891b2",
    "dark gray": "#6b7280",
    "dark grey": "#6b7280",
    "gray": "#9ca3af",
    "grey": "#9ca3af",
    "green": "#16a34a",
    "light gray": "#d1d5db",
    "light grey": "#d1d5db",
    "light-gray": "#d1d5db",
    "light-grey": "#d1d5db",
    "magenta": "#db2777",
    "orange": "#ea580c",
    "pink": "#db2777",
    "purple": "#7c3aed",
    "red": "#dc2626",
    "white": "#ffffff",
    "yellow": "#ca8a04",
}


def normalize_color(value: str | None, fallback: str) -> str:
    """Convert informal model color text into a renderer-safe hex color."""
    if not value:
        return fallback
    candidate = value.strip().lower()
    candidate = re.sub(r"\s+", " ", candidate)
    if candidate in COLOR_ALIASES:
        return COLOR_ALIASES[candidate]
    compact = candidate.replace(" ", "")
    if compact in COLOR_ALIASES:
        return COLOR_ALIASES[compact]
    if re.fullmatch(r"#(?:[0-9a-f]{3}|[0-9a-f]{6})", compact):
        return compact
    try:
        rgb = ImageColor.getrgb(compact)
    except ValueError:
        return fallback
    return "#{:02x}{:02x}{:02x}".format(*rgb[:3])
