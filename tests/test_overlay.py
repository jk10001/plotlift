from __future__ import annotations

from pathlib import Path

from PIL import Image

from app.models import CalibrationPoint, ChartValue, NormPoint, PixelPoint, SeriesPoint, SeriesState
from app.overlay import COLORS, _overlay_scale, render_calibration_overlay, render_series_overlay


def test_llm_overlay_scale_tracks_image_resolution_with_bounds() -> None:
    assert _overlay_scale((80, 80)) == 1.0
    assert _overlay_scale((1050, 570)) == 1.0
    assert _overlay_scale((2671, 1774)) == 1774 / 600
    assert _overlay_scale((12000, 9000)) == 6.0


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


def test_calibration_review_x_marker_grows_with_high_resolution_image(tmp_path: Path) -> None:
    low_image_path = tmp_path / "low.png"
    high_image_path = tmp_path / "high.png"
    low_overlay_path = tmp_path / "low_overlay.png"
    high_overlay_path = tmp_path / "high_overlay.png"
    Image.new("RGB", (600, 600), "black").save(low_image_path)
    Image.new("RGB", (1800, 1800), "black").save(high_image_path)

    def point_at(pixel: int) -> CalibrationPoint:
        return CalibrationPoint(
            label="x1",
            crop_image_norm=NormPoint(x=500, y=500),
            crop_image_px=PixelPoint(x=pixel, y=pixel),
            chart_value=ChartValue(value_raw="0", value_type="number", parsed_value=0),
        )

    render_calibration_overlay(low_image_path, [point_at(300)], low_overlay_path)
    render_calibration_overlay(high_image_path, [point_at(900)], high_overlay_path)

    with Image.open(low_overlay_path).convert("RGB") as low_overlay, Image.open(high_overlay_path).convert("RGB") as high_overlay:
        marker_color = Image.new("RGB", (1, 1), COLORS["calibration_review"]).getpixel((0, 0))
        assert low_overlay.getpixel((315, 315)) == (0, 0, 0)
        assert high_overlay.getpixel((915, 915)) == marker_color


def test_series_review_overlay_uses_red_x_markers_and_only_connects_lines(tmp_path: Path) -> None:
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
        assert all(
            scatter_overlay.getpixel((x, y)) != (255, 255, 255)
            for x in range(27, 40)
            for y in range(25, 40)
        )
