"""历史图纸附件：不进向量，检索时渲染成缩小 JPEG 给模型看模式。"""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image

_DRAWING_EXTS = frozenset({"pdf", "png", "jpg", "jpeg", "webp", "gif", "bmp"})
_MAX_PX = 1024
_JPEG_QUALITY = 72


def is_drawing_ext(ext: str | None) -> bool:
    return (ext or "").lower().lstrip(".") in _DRAWING_EXTS


def preview_jpeg_from_file(path: Path, *, ext: str | None = None, max_px: int = _MAX_PX) -> bytes:
    kind = (ext or path.suffix.lower().lstrip(".")).lower()
    if kind == "pdf":
        raw = _render_pdf_page(path, 0)
    else:
        raw = path.read_bytes()
    return _downscale_jpeg(raw, max_px=max_px)


def _render_pdf_page(pdf_path: Path, page_index: int) -> bytes:
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(pdf_path))
    try:
        page = doc[page_index]
        bitmap = page.render(scale=1.6)
        pil = bitmap.to_pil()
        buf = io.BytesIO()
        pil.convert("RGB").save(buf, format="JPEG", quality=85)
        return buf.getvalue()
    finally:
        doc.close()


def _downscale_jpeg(raw: bytes, *, max_px: int) -> bytes:
    image = Image.open(io.BytesIO(raw))
    image = image.convert("RGB")
    w, h = image.size
    longest = max(w, h)
    if longest > max_px:
        scale = max_px / float(longest)
        image = image.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=_JPEG_QUALITY, optimize=True)
    return buf.getvalue()
