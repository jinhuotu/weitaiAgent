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
    """第五章投标文件格式空白稿。优先用仓库内副本。"""
    bundled = Path(__file__).resolve().parent / "templates" / _CHAPTER5_NAME
    if bundled.is_file() and bundled.stat().st_size > 1000:
        return bundled
    stored = tender_assets_dir() / _CHAPTER5_NAME
    if stored.is_file() and stored.stat().st_size > 1000:
        return stored
    download = Path(r"E:\downLoad")
    if download.is_dir():
        found: list[Path] = []
        for item in download.iterdir():
            if item.suffix.lower() != ".docx" or item.name.startswith("~$"):
                continue
            if "2026-3-20" in item.name:
                found.append(item)
        if found:
            return max(found, key=lambda p: p.stat().st_size)
    return None


def find_quote_xlsx() -> Path | None:
    """招标工程量清单。优先仓库副本，其次 storage，再次本机招投标目录。"""
    bundled = Path(__file__).resolve().parent / "templates" / _QUOTE_NAME
    if bundled.is_file() and bundled.stat().st_size > 1000:
        return bundled
    stored = tender_assets_dir() / _QUOTE_NAME
    if stored.is_file() and stored.stat().st_size > 1000:
        return stored
    folder = Path(r"F:\伟泰\招投标文件")
    if folder.is_dir():
        found = [
            item
            for item in folder.iterdir()
            if item.suffix.lower() == ".xlsx"
            and "报价清单" in item.name
            and "20260416" in item.name
            and not item.name.startswith("~$")
        ]
        if found:
            return max(found, key=lambda p: p.stat().st_mtime)
    return None


def find_qualification_pdf() -> Path | None:
    """优先用 storage 内副本，其次本机 F:\\伟泰 下的最终版。"""
    bundled = tender_assets_dir() / _PDF_NAME
    if bundled.is_file() and bundled.stat().st_size > 1000:
        return bundled
    for candidate in (
        Path(r"F:\伟泰\伟泰科技资质文件最终版.pdf"),
        Path(r"F:\伟泰伟泰科技资质文件最终版.pdf"),
    ):
        if candidate.is_file() and candidate.stat().st_size > 1000:
            return candidate
    return None
