from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .color_utils import normalize_color
from .models import CalibrationPoint, PixelBBox, SeriesState


COLORS = {
    "crop": "#f59e0b",
    "x": "#2563eb",
    "y": "#dc2626",
    "series": ["#0891b2", "#7c3aed", "#16a34a", "#ea580c", "#db2777"],
    "text": "#111827",
    "white": "#ffffff",
}


def _font(size: int = 18) -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype("arial.ttf", size)
    except OSError:
        return ImageFont.load_default()


def _draw_label(
    draw: ImageDraw.ImageDraw,
    xy: tuple[float, float],
    label: str,
    fill: str,
    *,
    offset: tuple[int, int] = (8, -22),
) -> None:
    font = _font(18)
    x, y = xy
    text_xy = (x + offset[0], y + offset[1])
    bbox = draw.textbbox(text_xy, label, font=font)
    pad = 4
    draw.rectangle((bbox[0] - pad, bbox[1] - pad, bbox[2] + pad, bbox[3] + pad), fill=COLORS["white"], outline=fill, width=2)
    draw.text(text_xy, label, fill=fill, font=font)


def _draw_small_text(draw: ImageDraw.ImageDraw, xy: tuple[float, float], text: str, fill: str) -> None:
    font = _font(12)
    x, y = xy
    text_xy = (x + 7, y + 5)
    bbox = draw.textbbox(text_xy, text, font=font)
    pad = 2
    draw.rectangle((bbox[0] - pad, bbox[1] - pad, bbox[2] + pad, bbox[3] + pad), fill=COLORS["white"])
    draw.text(text_xy, text, fill=fill, font=font)


def _draw_x_marker(
    draw: ImageDraw.ImageDraw,
    xy: tuple[float, float],
    fill: str,
    size: int = 8,
    *,
    halo_width: int = 4,
    line_width: int = 2,
) -> None:
    x, y = xy
    half = size / 2
    for width, color in [(halo_width, COLORS["white"]), (line_width, fill)]:
        draw.line((x - half, y - half, x + half, y + half), fill=color, width=width)
        draw.line((x - half, y + half, x + half, y - half), fill=color, width=width)


def render_crop_overlay(image_path: Path, bbox: PixelBBox, target_path: Path) -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(image_path).convert("RGBA") as img:
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        rect = (bbox.left, bbox.top, bbox.right, bbox.bottom)
        draw.rectangle(rect, fill=(245, 158, 11, 34))
        draw.rectangle(rect, outline=COLORS["white"], width=10)
        draw.rectangle(rect, outline=COLORS["crop"], width=6)
        _draw_label(draw, (bbox.left, bbox.top), "crop", COLORS["crop"])
        Image.alpha_composite(img, overlay).convert("RGB").save(target_path)


def render_calibration_overlay(
    crop_image_path: Path,
    points: list[CalibrationPoint],
    target_path: Path,
) -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(crop_image_path).convert("RGBA") as img:
        draw = ImageDraw.Draw(img)
        for point in points:
            if not point.crop_image_px:
                continue
            color = COLORS["x"] if point.label.startswith("x") else COLORS["y"]
            x, y = point.crop_image_px.x, point.crop_image_px.y
            _draw_x_marker(draw, (x, y), color, size=14, halo_width=4, line_width=2)
            _draw_label(draw, (x, y), point.label, color, offset=(14, -30))
        if len(points) == 2 and all(point.crop_image_px for point in points):
            p1, p2 = points[0].crop_image_px, points[1].crop_image_px
            color = COLORS["x"] if points[0].label.startswith("x") else COLORS["y"]
            draw.line((p1.x, p1.y, p2.x, p2.y), fill=color, width=3)
        img.convert("RGB").save(target_path)


def render_series_overlay(
    crop_image_path: Path,
    series: list[SeriesState],
    target_path: Path,
) -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(crop_image_path).convert("RGBA") as img:
        draw = ImageDraw.Draw(img)
        for series_index, item in enumerate(series):
            color = normalize_color(item.line_color, COLORS["series"][series_index % len(COLORS["series"])])
            by_segment: dict[int, list[tuple[float, float, int]]] = {}
            for point in item.points:
                if not point.crop_image_px:
                    continue
                by_segment.setdefault(point.segment_index, []).append((point.crop_image_px.x, point.crop_image_px.y, point.point_index))
            for points in by_segment.values():
                points.sort(key=lambda pair: pair[2])
                xy = [(x, y) for x, y, _ in points]
                if len(xy) >= 2:
                    draw.line(xy, fill=color, width=4)
                for x, y, idx in points:
                    _draw_x_marker(draw, (x, y), color)
                    _draw_small_text(draw, (x, y), str(idx), color)
        img.convert("RGB").save(target_path)
