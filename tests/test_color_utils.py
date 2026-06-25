from __future__ import annotations

from pathlib import Path

from PIL import Image

from app.color_utils import normalize_color
from app.models import NormPoint, PixelPoint, SeriesPoint, SeriesState
from app.overlay import render_series_overlay


def test_normalize_informal_gray() -> None:
    assert normalize_color("light gray", "#000000") == "#d1d5db"
    assert normalize_color("light grey", "#000000") == "#d1d5db"


def test_normalize_accepts_hex_and_pillow_named_color() -> None:
    assert normalize_color("#2563eb", "#000000") == "#2563eb"
    assert normalize_color("darkgreen", "#000000") == "#006400"


def test_invalid_color_uses_fallback() -> None:
    assert normalize_color("not a real color", "#123456") == "#123456"


def test_series_overlay_tolerates_informal_model_color(tmp_path: Path) -> None:
    image_path = tmp_path / "crop.png"
    target_path = tmp_path / "overlay.png"
    Image.new("RGB", (100, 100), "white").save(image_path)
    series = SeriesState(
        id="s1",
        name="Head",
        line_color="light gray",
        points=[
            SeriesPoint(point_index=0, crop_image_norm=NormPoint(x=0, y=0), crop_image_px=PixelPoint(x=10, y=10)),
            SeriesPoint(point_index=1, crop_image_norm=NormPoint(x=999, y=999), crop_image_px=PixelPoint(x=90, y=90)),
        ],
    )
    render_series_overlay(image_path, [series], target_path)
    assert target_path.exists()
