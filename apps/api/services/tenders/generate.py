"""生成投标 Word，并可选复制资质 PDF。"""

from __future__ import annotations

import re
import shutil
import logging
from datetime import date
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

logger = logging.getLogger("api.tenders.generate")

_FILE_RE = re.compile(r"^[a-f0-9]{12}(?:\.format)?\.(docx|pdf)$", re.I)
_UNSAFE = re.compile(r'[\\/:*?"<>|\s]+')


def _cn_ordinal_text(number: int) -> str:
    if number <= 10:
        return "一二三四五六七八九十"[number - 1]
    if number < 20:
        return "十" + ("" if number == 10 else "一二三四五六七八九"[number - 11])
    return str(number)


def _renumber_rendered_chapters(document) -> int:
    """Normalize copied-format TOC and body chapter labels using one sequence."""
    import re

    heading = re.compile(r"^\s*[一二三四五六七八九十百零〇0-9]+、")
    toc = [p for p in document.paragraphs if "\t" in (p.text or "") and heading.match(p.text or "")]
    body = [
        p for p in document.paragraphs
        if "\t" not in (p.text or "") and heading.match(p.text or "")
        and (str(getattr(p.style, "name", "") or "").lower().startswith(("heading", "标题"))
             or len((p.text or "").strip()) <= 60)
    ]
    changed = 0
    for group in (toc, body):
        for index, para in enumerate(group, 1):
            prefix = f"{_cn_ordinal_text(index)}、"
            replaced = False
            for run in para.runs:
                if run.text and heading.match(run.text):
                    run.text = heading.sub(prefix, run.text, count=1)
                    replaced = True
                    break
            if replaced:
                changed += 1
    return changed


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


def _scoring_texts_for_quality(
    dest: Path,
    catalog_facts: list | None,
    generation_metadata: dict | None = None,
) -> list[str]:
    try:
        from docx import Document

        paragraphs = [str(p.text or "").strip() for p in Document(str(dest)).paragraphs]
        sections = (generation_metadata or {}).get("sections") if isinstance(generation_metadata, dict) else None
        if isinstance(sections, list):
            selected: list[str] = []
            for section in sections:
                if not isinstance(section, dict):
                    continue
                title = str(section.get("section") or "").strip()
                wanted = len(section.get("items") or [])
                if not title or not wanted:
                    continue
                start = next(
                    (
                        i for i in range(len(paragraphs) - 1, -1, -1)
                        if paragraphs[i].split("\t", 1)[0].strip() == title
                    ),
                    None,
                )
                if start is None:
                    continue
                end = len(paragraphs)
                for i in range(start + 1, len(paragraphs)):
                    if re.match(r"^[一二三四五六七八九十百]+、", paragraphs[i]):
                        end = i
                        break
                candidates = [
                    text for text in paragraphs[start + 1:end]
                    if (len(text) >= 60 or "我方针对" in text) and "我方" in text
                    and not text.startswith(("投标人", "法定代表人", "日期"))
                ]
                selected.extend(candidates[:wanted])
            if len(selected) >= 2:
                return selected
        candidates = [
            text for text in paragraphs
            if (len(text) >= 60 or "我方针对" in text) and "我方" in text
            and not text.startswith(("投标人", "法定代表人", "日期"))
        ]
        if len(candidates) >= 2:
            return candidates
    except Exception:
        pass
    texts: list[str] = []
    for fact in catalog_facts or []:
        if not isinstance(fact, dict) or not isinstance(fact.get("_scoring_drafts"), dict):
            continue
        for value in fact["_scoring_drafts"].values():
            if isinstance(value, dict):
                value = value.get("text") or ""
            if str(value or "").strip():
                texts.append(str(value).strip())
    return texts


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

    # 正式生成的封面日期取生成当天；不让草稿中的旧日期进入新产物。
    brief.bidDate = date.today().isoformat()
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
    from docx import Document
    from docx.oxml.ns import qn
    from api.services.tenders.document import (
        clean_visible_prompts,
        normalize_auth_role_paragraphs,
        normalize_signoff_block,
    )
    from api.services.tenders.pdf_format import _fill_bid_bond_paragraph

    post = Document(dest)
    if brief.bidBondYuan is not None:
        for element in post.element.body.iter(qn("w:p")):
            _fill_bid_bond_paragraph(element, brief)
    chapter_count = _renumber_rendered_chapters(post)
    auth_count = normalize_auth_role_paragraphs(post, brief)
    clean_visible_prompts(post)
    normalize_signoff_block(post.paragraphs)
    if chapter_count or auth_count or brief.bidBondYuan is not None:
        post.save(dest)
    if chapter_count:
        warnings.append(f"正文与目录章节序号已按骨架连续重编 {chapter_count} 处")
    if auth_count:
        warnings.append(f"授权委托书角色槽位按法人/代理人字段重写 {auth_count} 段")
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

        from api.services.tenders.native_format import reconcile_toc_references, validate_native_toc

        # 校验前兜底：前面任何一步（表单清理、分页归一）删掉了正文里带书签的段落，
        # 目录行就会变成指向不存在目标的孤儿行。这里按「目标书签是否存在」统一处理，
        # 避免一条陈旧目录条目把整次生成打成 422。
        checked = Document(dest)
        toc_fix = reconcile_toc_references(checked)
        if toc_fix["rebound"] or toc_fix["dropped"]:
            checked.save(dest)
            warnings.append(
                "目录条目校正：重绑 {} 条，删除 {} 条".format(
                    toc_fix["rebound"], toc_fix["dropped"]
                )
            )
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

    from api.services.tenders.scoring_points import scoring_uniqueness
    from api.services.tenders.qa import load_qa_bundle

    scoring_texts = _scoring_texts_for_quality(
        dest,
        catalog_facts,
        getattr(brief, "scoringMetadata", None),
    )
    technical_qa = load_qa_bundle(stem).get(vol, {}) if vol == "technical" else {}
    scoring_quality = technical_qa.get("scoringUniqueness") or scoring_uniqueness(scoring_texts)
    logger.warning(
        "scoring uniqueness: max Jaccard = %.3f (pair=%s), dup_count=%d, severe_count=%d",
        scoring_quality["max_jaccard"], scoring_quality["max_pair"],
        scoring_quality["dup_count"], scoring_quality["severe_count"],
    )
    if scoring_quality["severe_count"] > 0 and "内容重复度超标" not in warnings:
        warnings.append("内容重复度超标")
    scoring_generation = technical_qa.get("scoringGeneration") or getattr(brief, "scoringMetadata", {})
    if any(
        item.get("degraded")
        for section in (scoring_generation.get("sections", []) if isinstance(scoring_generation, dict) else [])
        if isinstance(section, dict)
        for item in (section.get("items", []) or [])
        if isinstance(item, dict)
    ):
        warnings.append("评分项正文生成不足，已按兜底模式输出，请核对")
    try:
        from api.services.tenders.qa import save_scoring_generation_quality

        save_scoring_generation_quality(
            stem,
            vol,
            scoring_quality,
            generation=scoring_generation,
        )
    except Exception:
        logger.exception("评分重复度 QA sidecar 写入失败")

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
        "scoringUniqueness": scoring_quality,
        "scoringGeneration": scoring_generation,
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
