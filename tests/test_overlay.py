from __future__ import annotations

from pathlib import Path

from PIL import Image

from app.models import CalibrationPoint, ChartValue, NormPoint, PixelPoint, SeriesPoint, SeriesState
from app.overlay import COLORS, render_calibration_overlay, render_series_overlay


def test_calibration_overlay_uses_hollow_circle_with_centered_cross(tmp_path: Path) -> None:
    image_path = tmp_path / "crop.png"
    target_path = tmp_path / "overlay.png"
    Image.new("RGB", (80, 80), "black").save(image_path)
    point = CalibrationPoint(
        label="x1",
        crop_image_norm=NormPoint(x=250, y=250),
        crop_image_px=PixelPoint(x=20, y=20),
        chart_value=ChartValue(value_raw="0", value_type="number", parsed_value=0),
    )

    render_calibration_overlay(image_path, [point], target_path)

    with Image.open(target_path).convert("RGB") as overlay:
        marker_color = Image.new("RGB", (1, 1), COLORS["x"]).getpixel((0, 0))
        assert overlay.getpixel((20, 20)) == marker_color
        assert overlay.getpixel((29, 20)) == marker_color
        assert overlay.getpixel((32, 20)) == marker_color
        assert overlay.getpixel((20, 32)) == marker_color
        for window_pixel in [(17, 17), (23, 17), (17, 23), (23, 23)]:
            assert overlay.getpixel(window_pixel) == (0, 0, 0)


def test_scatter_overlay_does_not_connect_markers(tmp_path: Path) -> None:
    image_path = tmp_path / "crop.png"
    line_path = tmp_path / "line.png"
    scatter_path = tmp_path / "scatter.png"
    Image.new("RGB", (100, 100), "black").save(image_path)
    points = [
        SeriesPoint(point_index=0, crop_image_norm=NormPoint(x=200, y=200), crop_image_px=PixelPoint(x=20, y=20)),
        SeriesPoint(point_index=1, crop_image_norm=NormPoint(x=800, y=800), crop_image_px=PixelPoint(x=80, y=80)),
    ]

    render_series_overlay(image_path, [SeriesState(id="line", name="Line", line_color="red", points=points)], line_path)
    render_series_overlay(
        image_path,
        [SeriesState(id="scatter", name="Scatter", series_type="scatter", marker_style="circle", line_color="red", points=points)],
        scatter_path,
    )

    with Image.open(line_path).convert("RGB") as line_overlay, Image.open(scatter_path).convert("RGB") as scatter_overlay:
        assert line_overlay.getpixel((50, 50)) != (0, 0, 0)
        assert scatter_overlay.getpixel((50, 50)) == (0, 0, 0)
