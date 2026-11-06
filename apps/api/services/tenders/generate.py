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
        "pathHint": str(path) if path else "storage/tender-assets/qualifications.pdf",
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
    word_bindings: list[dict] | None = None,
    word_sources: dict | None = None,
) -> dict[str, object]:
    from api.services.tenders.native_format import prefer_native_format
    from api.services.tenders.material_intents import prepare_structural_demands, coverage

    prepare_structural_demands(brief,catalog_slots)
    native = prefer_native_format(brief)
    vol = generate_volume(brief)
    stem = uuid4().hex[:12]
    out_dir = tenders_output_dir()
    dest = out_dir / f"{stem}.docx"
    pdf_src = find_qualification_pdf() if brief.attachQualifications and vol == "business" else None
    attachment_notes = []
    if pdf_src is not None:
        from api.services.tenders.assets import qualification_matches_company

        if not qualification_matches_company(pdf_src, brief.bidderName):
            attachment_notes.append('资质附件所属公司不符或尚未核验，未采用；请在资料库按本次投标人上传并核验')
            pdf_src = None
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
    word_outcomes = []
    if word_bindings:
        from docx import Document
        from api.services.cablepkg.word_materials import apply
        from api.services.tenders.native_format import _import_form_xml
        document = Document(dest)
        selected = [binding for binding in word_bindings if vol == 'full' or binding.get('volume') in {vol, 'full', ''}]
        word_outcomes = apply(document, selected, word_sources or {}, import_xml=_import_form_xml,
                              section_titles=[item.rawTitle or item.title for item in brief.outlineItems])
        for row in word_outcomes:
            if row['status'] in {'copied', 'filled'}:
                warnings.append('已复用 Word 资料：' + row['targetTitle'] + '；测试模板及待填字段须人工确认')
            elif row['status'] == 'needs_review':
                warnings.append('Word 资料待核对：' + row['targetTitle'] + '；' + row.get('reason', '未找到可填字段'))
        document.save(dest)
    from docx import Document
    from api.services.cablepkg.word_materials import prune_placeholder_boxes
    cleaned_document = Document(dest)
    removed_boxes = prune_placeholder_boxes(cleaned_document, word_outcomes, word_bindings or [])
    if removed_boxes:
        cleaned_document.save(dest)
    table_outcomes = []
    if brief.bidTableImportId:
        from api.services.bid_table_import import fill_document
        warnings.extend(fill_document(dest, brief.bidTableImportId, brief.invitationId, vol, outcomes=table_outcomes))
    from api.services.tenders.invitation_attachment import insert_required_invitation
    warnings.extend(insert_required_invitation(dest, brief.invitationId))
    # Reconcile only after all table/content writers; preserve source tables and
    # unrelated missing evidence. This does not change bid eligibility.
    from api.services.cablepkg.form_reconcile import reconcile_form_placeholders
    from api.services.tenders.directory_guard import directory_blocks
    final_document = Document(dest)
    form_cleanup = reconcile_form_placeholders(final_document, table_outcomes, excluded=directory_blocks(final_document))
    from api.services.cablepkg.form_pagination import normalize_duplicate_page_breaks
    pagination_cleanup = normalize_duplicate_page_breaks(final_document)
    if form_cleanup or pagination_cleanup:
        final_document.save(dest)
    warnings.extend(n for n in brief.materialIntentNotes if n not in warnings)
    warnings.extend(attachment_notes)
    from api.services.tenders.company_identity import missing_company_notes
    warnings.extend(n for n in missing_company_notes(brief) if n not in warnings)
    if native:
        from docx import Document

        from api.services.tenders.native_format import validate_native_toc

        validate_native_toc(Document(dest))

    from api.services.cablepkg.image_quality import inspect_docx

    image_quality = inspect_docx(dest)
    for image in image_quality["images"]:
        if image.get("lowResolution"):
            warnings.append(
                f"附件图片清晰度不足：{image['part']}，像素 {image['pixels']}，"
                f"排版 {image['widthCm']}×{image['heightCm']} cm，"
                f"有效 DPI {image['effectiveDpi']}，建议至少 220 DPI。"
            )
        elif image.get("error"):
            warnings.append(f"附件图片检查失败：{image['part']}，{image['error']}")

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
        "imageQuality": {vol: image_quality},
        "wordMaterialOutcomes": word_outcomes,
        "formPlaceholderCleanup": {vol: form_cleanup},
        "defaultsUsed": brief.bidderName,
        "generateVolume": vol,
        "materialCoverage": {vol:coverage(brief,vol)},
        "materialIntentComplete": brief.materialIntentComplete,
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
    out['materialCoverage'] = {**(base.get('materialCoverage') or {}), **(extra.get('materialCoverage') or {})}
    out["imageQuality"] = {**(base.get("imageQuality") or {}), **(extra.get("imageQuality") or {})}
    out["wordMaterialOutcomes"] = [*(base.get("wordMaterialOutcomes") or []), *(extra.get("wordMaterialOutcomes") or [])]
    out["formPlaceholderCleanup"] = {**(base.get("formPlaceholderCleanup") or {}), **(extra.get("formPlaceholderCleanup") or {})}
    out["generateBoth"] = True
    return out


def generate_pair(
    brief: BidBrief,
    *,
    catalog_slots: list[PlaceholderItem] | None = None,
    catalog_media: dict[str, list[Path]] | None = None,
    catalog_facts: list | None = None,
    word_bindings: list[dict] | None = None,
    word_sources: dict | None = None,
) -> dict[str, object]:
    """先出商务卷，再出技术卷。显式分册或建议分册时裁分商务卷。"""
    kw = dict(
        catalog_slots=catalog_slots,
        catalog_media=catalog_media,
        catalog_facts=catalog_facts,
        word_bindings=word_bindings,
        word_sources=word_sources,
    )
    # 报价留在商务卷，不另出一册。显式分册优先于识别建议，普通单卷调用保持原样。
    slice_biz = bool(brief.splitSlice) or (getattr(brief, "volumeAdvice", None) or "").strip() == "split"
    biz = brief.model_copy(
        update={"generateVolume": "business", "generateBoth": False, "splitSlice": slice_biz}
    )
    tech = brief.model_copy(
        update={"generateVolume": "technical", "generateBoth": False, "splitSlice": False}
    )
    business = generate_bid(biz, **kw)
    technical = generate_bid(tech, **kw)
    from api.services.tenders.material_intents import sync_material_outcomes

    sync_material_outcomes(brief,biz)
    sync_material_outcomes(brief,tech)
    return merge_generated(business,technical)


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
