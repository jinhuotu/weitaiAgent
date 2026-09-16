"""只读预览 PDF：缓存命中 / 关闭转换器。"""

from __future__ import annotations

import pytest

from api.services.tenders.preview_pdf import ensure_preview_pdf, preview_pdf_path
from common.config import get_settings
from common.errors import AppError


def test_preview_pdf_path_next_to_docx(tmp_path) -> None:
    docx = tmp_path / "abc123.docx"
    assert preview_pdf_path(docx) == tmp_path / "abc123.preview.pdf"


def test_ensure_preview_pdf_reuses_fresh_file(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TENDER_PREVIEW_CONVERTER", "off")
    get_settings.cache_clear()
    try:
        docx = tmp_path / "abc123.docx"
        docx.write_bytes(b"PK fake")
        pdf = preview_pdf_path(docx)
        pdf.write_bytes(b"%PDF-1.4 cached preview file\n%%EOF\n")
        assert ensure_preview_pdf(docx) == pdf
        assert pdf.read_bytes().startswith(b"%PDF")
    finally:
        get_settings.cache_clear()


def test_ensure_preview_pdf_off_without_cache(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TENDER_PREVIEW_CONVERTER", "off")
    get_settings.cache_clear()
    try:
        docx = tmp_path / "abc123.docx"
        docx.write_bytes(b"PK fake")
        with pytest.raises(AppError) as ei:
            ensure_preview_pdf(docx)
        assert ei.value.status_code == 503
    finally:
        get_settings.cache_clear()
