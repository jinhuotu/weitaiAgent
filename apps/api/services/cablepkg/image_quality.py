"""Raster preparation and final Word image checks for cable bid attachments."""
from __future__ import annotations

import io
import math
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

TARGET_DPI = 300
MIN_DPI = 220


def image_png(image, *, width_cm: float, height_cm: float) -> io.BytesIO:
    """Downsample only when the placed image exceeds the target pixel density."""
    image = ImageOps.exif_transpose(image).convert("RGB")
    width_px = max(1, math.ceil(width_cm / 2.54 * TARGET_DPI))
    height_px = max(1, math.ceil(height_cm / 2.54 * TARGET_DPI))
    ratio = min(1.0, width_px / image.width, height_px / image.height)
    if ratio < 1:
        image = image.resize((max(1, round(image.width * ratio)),
                              max(1, round(image.height * ratio))), Image.Resampling.LANCZOS)
    result = io.BytesIO()
    image.save(result, format="PNG", dpi=(TARGET_DPI, TARGET_DPI))
    result.seek(0)
    return result


def pdf_page_png(page) -> io.BytesIO:
    bitmap = page.render(scale=TARGET_DPI / 72.0)
    try:
        image = bitmap.to_pil()
        result = io.BytesIO()
        image.save(result, format="PNG", dpi=(TARGET_DPI, TARGET_DPI))
        result.seek(0)
        return result
    finally:
        bitmap.close()


def inspect_docx(path: str | Path) -> dict:
    """Check each raster placement using pixels and OOXML display dimensions."""
    from docx import Document
    from docx.oxml.ns import qn

    document = Document(path)
    images = []
    parts = [document.part]
    parts.extend(part for part in document.part.related_parts.values()
                 if part.content_type in {
                     "application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml",
                     "application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml",
                 })
    for part in parts:
        for drawing in part.element.iter(qn("w:drawing")):
            extent = next(drawing.iter(qn("wp:extent")), None)
            blip = next(drawing.iter(qn("a:blip")), None)
            if extent is None or blip is None:
                continue
            rid = blip.get(qn("r:embed"))
            if not rid or rid not in part.related_parts:
                continue
            image_part = part.related_parts[rid]
            width = int(extent.get("cx", "0")) / 360000
            height = int(extent.get("cy", "0")) / 360000
            row = {"part": str(image_part.partname), "widthCm": round(width, 3),
                   "heightCm": round(height, 3)}
            try:
                with Image.open(io.BytesIO(image_part.blob)) as image:
                    if image.format in {"WMF", "EPS"}:
                        continue
                    row["pixels"] = [image.width, image.height]
                    row["effectiveDpi"] = [round(image.width * 2.54 / width, 1) if width > 0 else 0,
                                           round(image.height * 2.54 / height, 1) if height > 0 else 0]
                row["lowResolution"] = min(row["effectiveDpi"]) < MIN_DPI
            except (UnidentifiedImageError, OSError):
                row["error"] = "无法读取内嵌图片像素"
            images.append(row)
    return {"targetDpi": TARGET_DPI, "minimumDpi": MIN_DPI, "images": images,
            "lowResolutionCount": sum(bool(row.get("lowResolution")) for row in images),
            "unreadableCount": sum("error" in row for row in images)}
