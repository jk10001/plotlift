from __future__ import annotations

import base64
from pathlib import Path

import fitz
from PIL import Image, ImageOps

from .artifacts import relative_path
from .logging_utils import emit_event
from .models import Asset, PixelBBox


SUPPORTED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
SUPPORTED_UPLOAD_SUFFIXES = SUPPORTED_IMAGE_SUFFIXES | {".pdf"}


def image_size(path: Path) -> tuple[int, int]:
    with Image.open(path) as img:
        return img.size


def image_to_data_url(path: Path) -> str:
    suffix = path.suffix.lower()
    mime = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }.get(suffix, "image/png")
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def create_canonical_image(source_path: Path, target_path: Path) -> Asset:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source_path) as img:
        converted = ImageOps.exif_transpose(img).convert("RGBA")
        converted.save(target_path)
        width, height = converted.size
    return Asset(path=target_path.name, width=width, height=height)


def rasterize_pdf(pdf_path: Path, target_dir: Path, dpi: int, run_id: str) -> list[Asset]:
    target_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(pdf_path)
    pages: list[Asset] = []
    zoom = dpi / 72.0
    matrix = fitz.Matrix(zoom, zoom)
    for index, page in enumerate(doc, start=1):
        pix = page.get_pixmap(matrix=matrix, alpha=False)
        target = target_dir / f"page_{index:03d}.png"
        pix.save(target)
        asset = Asset(path=f"pages/{target.name}", width=pix.width, height=pix.height, label=f"Page {index}")
        pages.append(asset)
        emit_event(target_dir.parent, "ARTIFACT", f"Rasterized PDF page {index}", run_id=run_id, artifact_path=asset.path)
    doc.close()
    return pages


def crop_image(source_path: Path, bbox: PixelBBox, target_path: Path) -> Asset:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source_path) as img:
        width, height = img.size
        left = max(0, min(width - 1, round(bbox.left)))
        top = max(0, min(height - 1, round(bbox.top)))
        right = max(left + 1, min(width, round(bbox.right)))
        bottom = max(top + 1, min(height, round(bbox.bottom)))
        cropped = img.crop((left, top, right, bottom))
        cropped.save(target_path)
        crop_width, crop_height = cropped.size
    return Asset(path=target_path.as_posix(), width=crop_width, height=crop_height)


def asset_for_path(path: Path, root: Path, label: str | None = None) -> Asset:
    width, height = image_size(path)
    return Asset(path=relative_path(path, root), width=width, height=height, label=label)
