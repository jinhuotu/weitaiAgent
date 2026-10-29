"""生成投标 Word，并可选复制资质 PDF。"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from uuid import uuid4

from api.services.tenders.assets import (
    chapter5_template_status,
    find_qualification_pdf,
    tenders_output_dir,
)
from api.services.tenders.schema import BidBrief, PlaceholderItem, default_brief
from api.services.tenders.slots import list_slots_status
from common.config import get_settings

_FILE_RE = re.compile(r"^[a-f0-9]{12}(?:\.format)?\.(docx|pdf)$", re.I)
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


def generate_volume(brief: BidBrief) -> str:
    v = (getattr(brief, "generateVolume", None) or "").strip()
    return v if v in {"business", "technical", "full"} else "business"


def generate_bid(
    brief: BidBrief,
    *,
    catalog_slots: list[PlaceholderItem] | None = None,
    catalog_media: dict[str, list[Path]] | None = None,
    catalog_facts: list | None = None,
) -> dict[str, object]:
    from api.services.tenders.native_format import prefer_native_format

    native = prefer_native_format(brief)
    vol = generate_volume(brief)
    stem = uuid4().hex[:12]
    out_dir = tenders_output_dir()
    dest = out_dir / f"{stem}.docx"
    pdf_src = find_qualification_pdf() if brief.attachQualifications and vol == "business" else None
    try:
        from api.services.tenders.assemble import assemble_bid_docx
    except ImportError as exc:
        from common.errors import AppError, ErrorCode

        raise AppError(
            ErrorCode.INTERNAL,
            "组卷模块加载失败。Windows 下改后端代码不会自动重载，请重启 API 后再点「生成并预览」。",
            status_code=500,
        ) from exc

    builder = assemble_bid_docx
    if brief.requirementsDriven:
        from api.services.tenders.document import build_bid_docx

        builder = build_bid_docx
    _, warnings = builder(
        brief,
        dest,
        qualification_pdf=pdf_src,
        catalog_slots=catalog_slots,
        catalog_media=catalog_media,
        catalog_facts=catalog_facts,
        volume=vol,
    )
    warnings = list(warnings)
    if native:
        from docx import Document

        from api.services.tenders.native_format import validate_native_toc

        validate_native_toc(Document(dest))

    pdf_name = ""
    if pdf_src is not None:
        pdf_name = f"{stem}.pdf"
        shutil.copy2(pdf_src, out_dir / pdf_name)

    out: dict[str, object] = {
        "docxFile": None,
        "techDocxFile": None,
        "pdfFile": None,
        "downloadName": "",
        "techDownloadName": None,
        "pdfDownloadName": None,
        "warnings": warnings,
        "defaultsUsed": brief.bidderName,
        "generateVolume": vol,
    }
    if vol == "technical":
        out["techDocxFile"] = dest.name
        out["techDownloadName"] = safe_download_name(brief.projectName, suffix="技术标.docx")
    elif vol == "full":
        out["docxFile"] = dest.name
        out["downloadName"] = safe_download_name(brief.projectName, suffix="投标文件.docx")
    else:
        out["docxFile"] = dest.name
        out["downloadName"] = safe_download_name(brief.projectName, suffix="商务标.docx")
        out["pdfFile"] = pdf_name or None
        out["pdfDownloadName"] = (
            safe_download_name(brief.projectName, suffix="资质文件.pdf") if pdf_name else None
        )
    return out


_MERGE_KEYS = (
    "docxFile",
    "techDocxFile",
    "pdfFile",
    "downloadName",
    "techDownloadName",
    "pdfDownloadName",
)


def merge_generated(base: dict[str, object], extra: dict[str, object]) -> dict[str, object]:
    out = dict(base)
    for key in _MERGE_KEYS:
        if extra.get(key) and not out.get(key):
            out[key] = extra[key]
    notes = list(out.get("warnings") or [])
    for note in extra.get("warnings") or []:
        if note not in notes:
            notes.append(note)
    out["warnings"] = notes
    out["generateBoth"] = True
    return out


def generate_pair(
    brief: BidBrief,
    *,
    catalog_slots: list[PlaceholderItem] | None = None,
    catalog_media: dict[str, list[Path]] | None = None,
    catalog_facts: list | None = None,
) -> dict[str, object]:
    """先出商务卷，再出技术卷。显式分册或建议分册时裁分商务卷。"""
    kw = dict(
        catalog_slots=catalog_slots,
        catalog_media=catalog_media,
        catalog_facts=catalog_facts,
    )
    # 报价留在商务卷，不另出一册。显式分册优先于识别建议，普通单卷调用保持原样。
    slice_biz = bool(brief.splitSlice) or (getattr(brief, "volumeAdvice", None) or "").strip() == "split"
    biz = brief.model_copy(
        update={"generateVolume": "business", "generateBoth": False, "splitSlice": slice_biz}
    )
    tech = brief.model_copy(
        update={"generateVolume": "technical", "generateBoth": False, "splitSlice": False}
    )
    return merge_generated(generate_bid(biz, **kw), generate_bid(tech, **kw))


def volume_pair_status(vol: str, *, business_ready: bool, technical_ready: bool) -> dict[str, object]:
    if (vol or "").strip() == "full":
        return {
            "generateVolume": "full",
            "otherVolume": "",
            "otherVolumeReady": True,
            "otherVolumeNote": "",
        }
    cur = vol if vol in {"business", "technical"} else "business"
    other = "technical" if cur == "business" else "business"
    ready = technical_ready if other == "technical" else business_ready
    cur_label = "技术标" if cur == "technical" else "商务标"
    other_label = "技术标" if other == "technical" else "商务标"
    note = "" if ready else f"本次生成的是{cur_label}，{other_label}尚未生成"
    return {
        "generateVolume": cur,
        "otherVolume": other,
        "otherVolumeReady": ready,
        "otherVolumeNote": note,
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
    data["chapter5Template"] = chapter5_template_status()
    data["slots"] = list_slots_status(extra)
    data["docPreview"] = get_settings().doc_preview_engine
    return data


async def defaults_payload_kb(
    db,
    extra: list[PlaceholderItem] | None = None,
    *,
    created_by: int | None = None,
) -> dict[str, object]:
    from api.services.tenders.library_kb import library_payload_kb, list_slots_status_kb

    del created_by
    data = defaults_payload(extra)
    data["slots"] = await list_slots_status_kb(db, extra)
    lib = await library_payload_kb(db)
    data["libraryBaseId"] = lib.get("baseId")
    return data
