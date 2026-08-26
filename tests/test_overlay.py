from __future__ import annotations

from pathlib import Path

from PIL import Image

from app.models import CalibrationPoint, ChartValue, NormPoint, PixelPoint, SeriesPoint, SeriesState
from app.overlay import COLORS, render_calibration_overlay, render_series_overlay


def test_calibration_review_overlay_uses_red_x_marker(tmp_path: Path) -> None:
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
        marker_color = Image.new("RGB", (1, 1), COLORS["calibration_review"]).getpixel((0, 0))
        assert overlay.getpixel((20, 20)) == marker_color
        assert any(overlay.getpixel((20 - offset, 20 - offset)) == marker_color for offset in range(2, 7))
        assert any(overlay.getpixel((20 + offset, 20 - offset)) == marker_color for offset in range(2, 7))
        for cardinal_pixel in [(20, 13), (13, 20), (27, 20), (20, 27)]:
            assert overlay.getpixel(cardinal_pixel) == (0, 0, 0)


def test_series_review_overlay_uses_fixed_red_x_markers_and_only_connects_lines(tmp_path: Path) -> None:
    image_path = tmp_path / "crop.png"
    line_path = tmp_path / "line.png"
    scatter_path = tmp_path / "scatter.png"
    Image.new("RGB", (100, 100), "black").save(image_path)
    points = [
        SeriesPoint(point_index=0, crop_image_norm=NormPoint(x=200, y=200), crop_image_px=PixelPoint(x=20, y=20)),
        SeriesPoint(point_index=1, crop_image_norm=NormPoint(x=800, y=800), crop_image_px=PixelPoint(x=80, y=80)),
    ]

    render_series_overlay(image_path, [SeriesState(id="line", name="Line", line_color="blue", points=points)], line_path)
    render_series_overlay(
        image_path,
        [SeriesState(id="scatter", name="Scatter", series_type="scatter", marker_style="circle", line_color="blue", points=points)],
        scatter_path,
    )

    with Image.open(line_path).convert("RGB") as line_overlay, Image.open(scatter_path).convert("RGB") as scatter_overlay:
        review_red = Image.new("RGB", (1, 1), COLORS["series_review"]).getpixel((0, 0))
        assert line_overlay.getpixel((50, 50)) == review_red
        assert scatter_overlay.getpixel((50, 50)) == (0, 0, 0)
        assert scatter_overlay.getpixel((20, 20)) == review_red
        assert any(
            scatter_overlay.getpixel((x, y)) == review_red
            for x in range(27, 40)
            for y in range(25, 40)
        )
