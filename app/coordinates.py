from __future__ import annotations

from datetime import datetime, timezone

from .models import (
    CalibratedAxis,
    CalibrationPoint,
    ChartValue,
    NormBBox,
    NormPoint,
    PixelBBox,
    PixelPoint,
    SeriesPoint,
)


def _extent(size: int) -> float:
    if size <= 1:
        raise ValueError("image dimensions must be greater than 1")
    return float(size - 1)


def norm_to_px_point(point: NormPoint, width: int, height: int) -> PixelPoint:
    return PixelPoint(x=point.x / 999.0 * _extent(width), y=point.y / 999.0 * _extent(height))


def px_to_norm_point(point: PixelPoint, width: int, height: int, clamp: bool = False) -> NormPoint:
    x = round(point.x / _extent(width) * 999)
    y = round(point.y / _extent(height) * 999)
    if clamp:
        x = min(999, max(0, x))
        y = min(999, max(0, y))
    return NormPoint(x=x, y=y)


def norm_to_px_bbox(bbox: NormBBox, width: int, height: int) -> PixelBBox:
    left_top = norm_to_px_point(NormPoint(x=bbox.left, y=bbox.top), width, height)
    right_bottom = norm_to_px_point(NormPoint(x=bbox.right, y=bbox.bottom), width, height)
    return PixelBBox(left=left_top.x, top=left_top.y, right=right_bottom.x, bottom=right_bottom.y)


def px_to_norm_bbox(bbox: PixelBBox, width: int, height: int, clamp: bool = False) -> NormBBox:
    left_top = px_to_norm_point(PixelPoint(x=bbox.left, y=bbox.top), width, height, clamp=clamp)
    right_bottom = px_to_norm_point(PixelPoint(x=bbox.right, y=bbox.bottom), width, height, clamp=clamp)
    return NormBBox(left=left_top.x, top=left_top.y, right=right_bottom.x, bottom=right_bottom.y)


def crop_to_full_point(point: PixelPoint, crop_bbox_full_px: PixelBBox) -> PixelPoint:
    return PixelPoint(x=point.x + crop_bbox_full_px.left, y=point.y + crop_bbox_full_px.top)


def full_to_crop_point(point: PixelPoint, crop_bbox_full_px: PixelBBox) -> PixelPoint:
    return PixelPoint(x=point.x - crop_bbox_full_px.left, y=point.y - crop_bbox_full_px.top)


def crop_norm_to_crop_px(point: NormPoint, crop_width: int, crop_height: int) -> PixelPoint:
    return norm_to_px_point(point, crop_width, crop_height)


def crop_px_to_crop_norm(point: PixelPoint, crop_width: int, crop_height: int, clamp: bool = False) -> NormPoint:
    return px_to_norm_point(point, crop_width, crop_height, clamp=clamp)


def chart_value_to_scalar(value: ChartValue) -> float:
    if value.value_type == "number":
        if value.parsed_value is None:
            raise ValueError(f"missing parsed numeric value for {value.value_raw!r}")
        return float(value.parsed_value)
    if value.value_type == "datetime":
        if not value.parsed_datetime:
            raise ValueError(f"missing parsed datetime for {value.value_raw!r}")
        parsed = datetime.fromisoformat(value.parsed_datetime.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    raise ValueError(f"unsupported chart value type: {value.value_type}")


def scalar_to_chart_value(scalar: float, template: ChartValue) -> ChartValue:
    if template.value_type == "datetime":
        dt = datetime.fromtimestamp(scalar, tz=timezone.utc)
        iso = dt.isoformat().replace("+00:00", "Z")
        return ChartValue(value_raw=iso, value_type="datetime", parsed_datetime=iso, unit=template.unit)
    return ChartValue(value_raw=f"{scalar:g}", value_type="number", parsed_value=scalar, unit=template.unit)


def linear_map(value: float, src1: float, src2: float, dst1: float, dst2: float) -> float:
    if src1 == src2:
        raise ValueError("calibration source points have zero distance")
    if dst1 == dst2:
        raise ValueError("calibration chart-space values are duplicates")
    ratio = (value - src1) / (src2 - src1)
    return dst1 + ratio * (dst2 - dst1)


def validate_axis_geometry(points: list[CalibrationPoint], axis: str, tolerance_px: float = 4.0) -> list[str]:
    if len(points) != 2:
        raise ValueError(f"{axis}-axis calibration requires exactly two points")
    p1, p2 = points
    if p1.crop_image_px is None or p2.crop_image_px is None:
        raise ValueError("calibration points require pixel coordinates")
    warnings: list[str] = []
    if axis == "x":
        delta = abs(p1.crop_image_px.y - p2.crop_image_px.y)
        if delta > tolerance_px:
            raise ValueError(f"x calibration points are not horizontal; y differs by {delta:.2f}px")
        if abs(p1.crop_image_px.x - p2.crop_image_px.x) <= tolerance_px:
            raise ValueError("x calibration points are too close together")
    elif axis == "y":
        delta = abs(p1.crop_image_px.x - p2.crop_image_px.x)
        if delta > tolerance_px:
            raise ValueError(f"y calibration points are not vertical; x differs by {delta:.2f}px")
        if abs(p1.crop_image_px.y - p2.crop_image_px.y) <= tolerance_px:
            raise ValueError("y calibration points are too close together")
    else:
        raise ValueError(f"unknown axis: {axis}")
    _ = chart_value_to_scalar(p1.chart_value)
    _ = chart_value_to_scalar(p2.chart_value)
    if chart_value_to_scalar(p1.chart_value) == chart_value_to_scalar(p2.chart_value):
        raise ValueError(f"{axis} calibration chart-space values must differ")
    return warnings


def image_point_to_chart_space(
    point: PixelPoint,
    x_points: list[CalibrationPoint],
    y_points: list[CalibrationPoint],
) -> tuple[ChartValue, ChartValue]:
    if len(x_points) != 2 or len(y_points) != 2:
        raise ValueError("both x and y calibration points are required")
    x1, x2 = x_points
    y1, y2 = y_points
    if not x1.crop_image_px or not x2.crop_image_px or not y1.crop_image_px or not y2.crop_image_px:
        raise ValueError("calibration points require pixel coordinates")

    x_scalar = linear_map(
        point.x,
        x1.crop_image_px.x,
        x2.crop_image_px.x,
        chart_value_to_scalar(x1.chart_value),
        chart_value_to_scalar(x2.chart_value),
    )
    y_scalar = linear_map(
        point.y,
        y1.crop_image_px.y,
        y2.crop_image_px.y,
        chart_value_to_scalar(y1.chart_value),
        chart_value_to_scalar(y2.chart_value),
    )
    return scalar_to_chart_value(x_scalar, x1.chart_value), scalar_to_chart_value(y_scalar, y1.chart_value)


def image_point_to_chart_space_for_axes(
    point: PixelPoint,
    x_axis: CalibratedAxis,
    y_axis: CalibratedAxis,
) -> tuple[ChartValue, ChartValue]:
    return image_point_to_chart_space(point, x_axis.points, y_axis.points)


def chart_space_to_image_point(
    chart_x: ChartValue,
    chart_y: ChartValue,
    x_points: list[CalibrationPoint],
    y_points: list[CalibrationPoint],
) -> PixelPoint:
    if len(x_points) != 2 or len(y_points) != 2:
        raise ValueError("both x and y calibration points are required")
    x1, x2 = x_points
    y1, y2 = y_points
    if not x1.crop_image_px or not x2.crop_image_px or not y1.crop_image_px or not y2.crop_image_px:
        raise ValueError("calibration points require pixel coordinates")
    x = linear_map(
        chart_value_to_scalar(chart_x),
        chart_value_to_scalar(x1.chart_value),
        chart_value_to_scalar(x2.chart_value),
        x1.crop_image_px.x,
        x2.crop_image_px.x,
    )
    y = linear_map(
        chart_value_to_scalar(chart_y),
        chart_value_to_scalar(y1.chart_value),
        chart_value_to_scalar(y2.chart_value),
        y1.crop_image_px.y,
        y2.crop_image_px.y,
    )
    return PixelPoint(x=x, y=y)


def chart_space_to_image_point_for_axes(
    chart_x: ChartValue,
    chart_y: ChartValue,
    x_axis: CalibratedAxis,
    y_axis: CalibratedAxis,
) -> PixelPoint:
    return chart_space_to_image_point(chart_x, chart_y, x_axis.points, y_axis.points)


def populate_calibration_pixels(points: list[CalibrationPoint], crop_width: int, crop_height: int) -> list[CalibrationPoint]:
    populated: list[CalibrationPoint] = []
    for point in points:
        copied = point.model_copy(deep=True)
        copied.crop_image_px = crop_norm_to_crop_px(point.crop_image_norm, crop_width, crop_height)
        populated.append(copied)
    return populated


def populate_series_point(
    point: SeriesPoint,
    crop_width: int,
    crop_height: int,
    x_points: list[CalibrationPoint] | None = None,
    y_points: list[CalibrationPoint] | None = None,
) -> SeriesPoint:
    copied = point.model_copy(deep=True)
    copied.crop_image_px = crop_norm_to_crop_px(point.crop_image_norm, crop_width, crop_height)
    if x_points and y_points:
        copied.chart_x, copied.chart_y = image_point_to_chart_space(copied.crop_image_px, x_points, y_points)
    return copied


def populate_series_point_for_axes(
    point: SeriesPoint,
    crop_width: int,
    crop_height: int,
    x_axis: CalibratedAxis | None = None,
    y_axis: CalibratedAxis | None = None,
) -> SeriesPoint:
    copied = point.model_copy(deep=True)
    copied.crop_image_px = crop_norm_to_crop_px(point.crop_image_norm, crop_width, crop_height)
    if x_axis and y_axis:
        copied.chart_x, copied.chart_y = image_point_to_chart_space_for_axes(copied.crop_image_px, x_axis, y_axis)
    return copied
