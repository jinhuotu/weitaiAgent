"""企业资质 PDF 查找。不入库知识库，生成时按页插入 Word。"""

from __future__ import annotations

from pathlib import Path

from common.config import get_settings

_PDF_NAME = "weitai-qualifications.pdf"
_CHAPTER5_NAME = "chapter5.docx"
_QUOTE_NAME = "quote.xlsx"


def tender_assets_dir() -> Path:
    root = Path(get_settings().storage_root).expanduser().resolve()
    path = root / "tender-assets"
    path.mkdir(parents=True, exist_ok=True)
    return path


def tenders_output_dir() -> Path:
    root = Path(get_settings().storage_root).expanduser().resolve()
    path = root / "tenders"
    path.mkdir(parents=True, exist_ok=True)
    return path


def find_chapter5_template() -> Path | None:
    """第五章投标文件格式空白稿。仅仓库内副本或 storage/tender-assets。"""
    bundled = Path(__file__).resolve().parent / "templates" / _CHAPTER5_NAME
    if bundled.is_file() and bundled.stat().st_size > 1000:
        return bundled
    stored = tender_assets_dir() / _CHAPTER5_NAME
    if stored.is_file() and stored.stat().st_size > 1000:
        return stored
    return None


def find_quote_xlsx() -> Path | None:
    """招标工程量清单。仅仓库副本或 storage/tender-assets。"""
    bundled = Path(__file__).resolve().parent / "templates" / _QUOTE_NAME
    if bundled.is_file() and bundled.stat().st_size > 1000:
        return bundled
    stored = tender_assets_dir() / _QUOTE_NAME
    if stored.is_file() and stored.stat().st_size > 1000:
        return stored
    return None


def find_qualification_pdf() -> Path | None:
    """企业资质 PDF：仅 storage/tender-assets/weitai-qualifications.pdf。"""
    bundled = tender_assets_dir() / _PDF_NAME
    if bundled.is_file() and bundled.stat().st_size > 1000:
        return bundled
    return None
