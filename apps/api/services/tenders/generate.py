"""生成投标 Word，并可选复制资质 PDF。"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from uuid import uuid4

from api.services.tenders.assets import find_qualification_pdf, tenders_output_dir
from api.services.tenders.document import build_bid_docx
from api.services.tenders.schema import BidBrief, PlaceholderItem, default_brief
from api.services.tenders.slots import list_slots_status

_FILE_RE = re.compile(r"^[a-f0-9]{12}\.(docx|pdf)$", re.I)
_UNSAFE = re.compile(r'[\\/:*?"<>|\s]+')


def safe_download_name(project_name: str, *, suffix: str) -> str:
    stem = _UNSAFE.sub("-", (project_name or "").strip())[:40].strip("-") or "bid"
    return f"{stem}-{suffix}"


def qualification_status() -> dict[str, object]:
    path = find_qualification_pdf()
    return {
        "found": path is not None,
        "pathHint": str(path) if path else "storage/tender-assets/weitai-qualifications.pdf",
        "sizeBytes": path.stat().st_size if path else 0,
    }


def generate_bid(
    brief: BidBrief,
    *,
    catalog_slots: list[PlaceholderItem] | None = None,
    catalog_media: dict[str, list[Path]] | None = None,
) -> dict[str, object]:
    stem = uuid4().hex[:12]
    out_dir = tenders_output_dir()
    docx_name = f"{stem}.docx"
    dest = out_dir / docx_name
    pdf_src = find_qualification_pdf() if brief.attachQualifications else None
    _, warnings = build_bid_docx(
        brief,
        dest,
        qualification_pdf=pdf_src,
        catalog_slots=catalog_slots,
        catalog_media=catalog_media,
    )

    pdf_name = ""
    if pdf_src is not None:
        pdf_name = f"{stem}.pdf"
        shutil.copy2(pdf_src, out_dir / pdf_name)

    return {
        "docxFile": docx_name,
        "pdfFile": pdf_name or None,
        "downloadName": safe_download_name(brief.projectName, suffix="投标文件.docx"),
        "pdfDownloadName": safe_download_name(brief.projectName, suffix="资质文件.pdf")
        if pdf_name
        else None,
        "warnings": warnings,
        "defaultsUsed": brief.bidderName,
    }


def resolve_output_file(file_name: str):
    from common.errors import AppError, ErrorCode

    name = (file_name or "").strip().replace("\\", "/").split("/")[-1]
    if not _FILE_RE.match(name):
        raise AppError(ErrorCode.BAD_REQUEST, "invalid tender file name", status_code=400)
    path = (tenders_output_dir() / name).resolve()
    root = tenders_output_dir()
    if not path.is_relative_to(root) or not path.is_file():
        raise AppError(ErrorCode.NOT_FOUND, "tender file not found", status_code=404)
    return path


def defaults_payload(extra: list[PlaceholderItem] | None = None) -> dict[str, object]:
    brief = default_brief()
    data = brief.model_dump()
    data["qualification"] = qualification_status()
    data["slots"] = list_slots_status(extra)
    return data


async def defaults_payload_kb(
    db,
    extra: list[PlaceholderItem] | None = None,
    *,
    created_by: int | None = None,
) -> dict[str, object]:
    from api.services.tenders.library_kb import library_payload_kb, list_slots_status_kb

    data = defaults_payload(extra)
    data["slots"] = await list_slots_status_kb(db, extra)
    lib = await library_payload_kb(db, created_by=created_by)
    data["libraryBaseId"] = lib.get("baseId")
    return data
