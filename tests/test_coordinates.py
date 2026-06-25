from __future__ import annotations

from app.coordinates import (
    chart_space_to_image_point,
    crop_to_full_point,
    image_point_to_chart_space,
    norm_to_px_bbox,
    norm_to_px_point,
    px_to_norm_point,
)
from app.models import CalibrationPoint, ChartValue, NormBBox, NormPoint, PixelBBox, PixelPoint


def test_norm_to_pixel_edges() -> None:
    assert norm_to_px_point(NormPoint(x=0, y=0), 101, 201) == PixelPoint(x=0, y=0)
    assert norm_to_px_point(NormPoint(x=999, y=999), 101, 201) == PixelPoint(x=100, y=200)


def test_pixel_to_norm_roundtrip() -> None:
    point = PixelPoint(x=50, y=100)
    norm = px_to_norm_point(point, 101, 201)
    assert norm == NormPoint(x=500, y=500)


def test_bbox_conversion() -> None:
    bbox = norm_to_px_bbox(NormBBox(left=0, top=0, right=999, bottom=999), 101, 201)
    assert bbox == PixelBBox(left=0, top=0, right=100, bottom=200)


def test_crop_to_full_point() -> None:
    full = crop_to_full_point(PixelPoint(x=10, y=20), PixelBBox(left=100, top=200, right=300, bottom=400))
    assert full == PixelPoint(x=110, y=220)


def test_chart_space_mapping_with_upward_y_axis() -> None:
    x_points = [
        CalibrationPoint(
            label="x1",
            crop_image_norm=NormPoint(x=0, y=0),
            crop_image_px=PixelPoint(x=100, y=500),
            chart_value=ChartValue(value_raw="0", value_type="number", parsed_value=0),
        ),
        CalibrationPoint(
            label="x2",
            crop_image_norm=NormPoint(x=0, y=0),
            crop_image_px=PixelPoint(x=500, y=500),
            chart_value=ChartValue(value_raw="100", value_type="number", parsed_value=100),
        ),
    ]
    y_points = [
        CalibrationPoint(
            label="y1",
            crop_image_norm=NormPoint(x=0, y=0),
            crop_image_px=PixelPoint(x=100, y=500),
            chart_value=ChartValue(value_raw="0", value_type="number", parsed_value=0),
        ),
        CalibrationPoint(
            label="y2",
            crop_image_norm=NormPoint(x=0, y=0),
            crop_image_px=PixelPoint(x=100, y=100),
            chart_value=ChartValue(value_raw="100", value_type="number", parsed_value=100),
        ),
    ]
    chart_x, chart_y = image_point_to_chart_space(PixelPoint(x=300, y=300), x_points, y_points)
    assert chart_x.parsed_value == 50
    assert chart_y.parsed_value == 50
    px = chart_space_to_image_point(chart_x, chart_y, x_points, y_points)
    assert px == PixelPoint(x=300, y=300)
