"""投标文件生成记录：入库、列表、详情。"""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.services.tenders.assets import tenders_output_dir
from api.services.tenders.generate import generate_bid
from api.services.tenders.schema import BidBrief, PlaceholderItem
from common.errors import AppError, ErrorCode
from common.times import to_epoch_ms
from db.models.tender import TenderRecord


def _record_to_dict(row: TenderRecord, *, include_brief: bool = False) -> dict[str, object]:
    docx_ok = (tenders_output_dir() / row.docx_file).is_file()
    pdf_ok = bool(row.pdf_file) and (tenders_output_dir() / str(row.pdf_file)).is_file()
    data: dict[str, object] = {
        "id": row.public_id,
        "projectName": row.project_name or "",
        "tenderer": row.tenderer or "",
        "bidPriceYuan": float(row.bid_price_yuan or 0),
        "legalPersonName": row.legal_person_name or "",
        "docxFile": row.docx_file,
        "pdfFile": row.pdf_file,
        "downloadName": row.download_name or "",
        "pdfDownloadName": row.pdf_download_name,
        "warnings": row.warnings if isinstance(row.warnings, list) else [],
        "username": row.username or "",
        "createdAt": to_epoch_ms(row.created_at),
        "docxAvailable": docx_ok,
        "pdfAvailable": pdf_ok,
    }
    if include_brief and isinstance(row.brief_json, dict):
        data["brief"] = row.brief_json
    return data


def _brief_generate_issues(brief: BidBrief) -> list[str]:
    issues: list[str] = []
    if not (brief.projectName or "").strip():
        issues.append("项目名称")
    if not (brief.tenderer or "").strip():
        issues.append("招标人")
    if not (float(brief.bidPriceYuan or 0) > 0):
        issues.append("投标总价")
    if not (brief.bidderName or "").strip():
        issues.append("投标人全称")
    if not (brief.bidderAddress or "").strip():
        issues.append("地址")
    if not (brief.foundedDate or "").strip():
        issues.append("成立日期")
    if not (brief.bidderPhone or "").strip():
        issues.append("电话")
    if not (brief.legalPersonName or "").strip():
        issues.append("法人姓名")
    if not (brief.legalPersonAge or "").strip():
        issues.append("年龄")
    if not (brief.legalPersonIdNo or "").strip():
        issues.append("法人身份证号")
    if (brief.agentName or "").strip() and not (brief.agentIdNo or "").strip():
        issues.append("代理人身份证号")
    if not any((ln.name or "").strip() for ln in (brief.quoteLines or [])):
        issues.append("报价清单")
    return issues


def _missing_required_titles(
    required_keys: list[str],
    slots: list[PlaceholderItem],
    media: dict,
) -> list[str]:
    titles = {item.key: item.title or item.key for item in slots}
    missing: list[str] = []
    for key in required_keys:
        files = media.get(key) if isinstance(media, dict) else None
        if not files:
            missing.append(titles.get(key) or key)
    return missing


def _missing_slot_warnings(missing: list[str]) -> list[str]:
    if not missing:
        return []
    return ["以下资料未上传，已在附件区用虚线框占位：" + "、".join(missing)]


async def create_record_from_generate(
    db: AsyncSession,
    brief: BidBrief,
    *,
    user_id: int,
    username: str,
) -> dict[str, object]:
    """生成 Word 并写入 tender_records。"""
    import time

    from api.services.tenders.extract import merge_performance_lines
    from api.services.tenders.library_kb import (
        catalog_placeholders,
        count_uncached_performance_files,
        list_library_items,
        performance_from_library,
        resolve_attachments,
    )
    from api.services.tenders.placeholders import collect_slots, this_bid_keys

    t0 = time.perf_counter()
    issues = _brief_generate_issues(brief)
    if issues:
        raise AppError(
            ErrorCode.VALIDATION,
            "请先完善：" + "、".join(issues),
            status_code=422,
        )

    catalog = await catalog_placeholders(db)
    t_catalog = time.perf_counter()
    # 生成路径禁止当场 OCR/LLM；未缓存的合同请在资料库上传时抽取
    lib_perf = await performance_from_library(db, extract_missing=False)
    pending_perf = await count_uncached_performance_files(db)
    if lib_perf:
        brief.performanceLines = merge_performance_lines(brief.performanceLines, lib_perf)
    has_agent = bool((brief.agentName or "").strip() or (brief.agentIdNo or "").strip())
    required_keys, include_keys = this_bid_keys(
        extra=brief.extraPlaceholders,
        catalog=catalog,
        required_keys=brief.requiredSlotKeys,
        include_keys=brief.includeSlotKeys,
        has_agent=has_agent,
    )
    brief.requiredSlotKeys = required_keys
    brief.includeSlotKeys = include_keys
    slots = collect_slots(brief.extraPlaceholders, catalog=catalog, include_keys=include_keys)
    media = await resolve_attachments(db, slots)
    from api.services.tenders.placeholders import TECH_DRAWING_KEY
    from api.services.tenders.slots import list_slot_files

    if TECH_DRAWING_KEY not in media:
        drawing_files = list_slot_files(TECH_DRAWING_KEY)
        if drawing_files:
            media[TECH_DRAWING_KEY] = drawing_files
    missing = _missing_required_titles(required_keys, slots, media)
    t_prep = time.perf_counter()
    generated = generate_bid(brief, catalog_slots=slots, catalog_media=media)
    t_docx = time.perf_counter()
    from api.services.tenders.categories import technical_soft_issues

    warnings = list(generated.get("warnings") or [])
    if pending_perf:
        warnings.append(
            f"类似业绩有 {pending_perf} 个合同/发票尚未抽出摘要，本次未做 OCR（避免拖慢生成）。"
            "可在资料库重新上传或稍后补抽后再生成。"
        )
    for note in _missing_slot_warnings(missing):
        if note not in warnings:
            warnings.append(note)
    for note in technical_soft_issues(
        brief, slots=slots, media=media, required_keys=required_keys
    ):
        if note not in warnings:
            warnings.append(note)
    # 复用已取的 catalog 槽位信息做匹配，避免再查一遍资料库
    library_items = [
        {
            "key": item.key,
            "title": item.title,
            "hint": item.hint,
            "fileCount": len(media.get(item.key) or []),
            "files": [],
        }
        for item in catalog
    ]
    # 补全未进 catalog 但在 media 里的 key（少见）
    from api.services.tenders.match import attachment_match_notes, build_attachment_match

    attachment_match = build_attachment_match(
        library_items or await list_library_items(db),
        required_keys=required_keys,
        include_keys=include_keys,
    )
    for note in attachment_match_notes(attachment_match):
        if note not in warnings:
            warnings.append(note)
    elapsed = time.perf_counter() - t0
    warnings.append(
        f"生成耗时约 {elapsed:.1f}s"
        f"（资料 {t_catalog - t0:.1f}s / 准备 {t_prep - t_catalog:.1f}s / Word {t_docx - t_prep:.1f}s）"
    )
    generated["warnings"] = warnings
    row = TenderRecord(
        public_id=uuid4().hex[:16],
        user_id=user_id,
        username=(username or "").strip()[:64],
        project_name=(brief.projectName or "").strip()[:256],
        tenderer=(brief.tenderer or "").strip()[:256],
        bid_price_yuan=float(brief.bidPriceYuan or 0),
        legal_person_name=(brief.legalPersonName or "").strip()[:64],
        docx_file=str(generated["docxFile"]),
        pdf_file=generated.get("pdfFile") or None,
        download_name=str(generated.get("downloadName") or ""),
        pdf_download_name=generated.get("pdfDownloadName") or None,
        warnings=list(generated.get("warnings") or []),
        brief_json=brief.model_dump(mode="json"),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    out = _record_to_dict(row)
    out["warnings"] = generated.get("warnings") or []
    out["defaultsUsed"] = generated.get("defaultsUsed")
    out["attachmentMatch"] = attachment_match
    return out


def _list_stmt(*, q: str | None, limit: int, offset: int) -> Select[tuple[TenderRecord]]:
    stmt = select(TenderRecord).order_by(TenderRecord.id.desc())
    keyword = (q or "").strip()
    if keyword:
        like = f"%{keyword}%"
        stmt = stmt.where(
            (TenderRecord.project_name.like(like))
            | (TenderRecord.tenderer.like(like))
            | (TenderRecord.username.like(like))
        )
    return stmt.offset(max(0, offset)).limit(max(1, min(limit, 100)))


async def list_records(
    db: AsyncSession,
    *,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, object]:
    from sqlalchemy import func

    base = select(func.count()).select_from(TenderRecord)
    keyword = (q or "").strip()
    if keyword:
        like = f"%{keyword}%"
        base = base.where(
            (TenderRecord.project_name.like(like))
            | (TenderRecord.tenderer.like(like))
            | (TenderRecord.username.like(like))
        )
    total = int((await db.execute(base)).scalar_one() or 0)
    rows = (await db.execute(_list_stmt(q=q, limit=limit, offset=offset))).scalars().all()
    return {
        "total": total,
        "items": [_record_to_dict(r) for r in rows],
    }


async def get_record(db: AsyncSession, public_id: str) -> dict[str, object]:
    pid = (public_id or "").strip()
    if not pid:
        raise AppError(ErrorCode.BAD_REQUEST, "missing record id", status_code=400)
    row = (
        await db.execute(select(TenderRecord).where(TenderRecord.public_id == pid))
    ).scalar_one_or_none()
    if row is None:
        raise AppError(ErrorCode.NOT_FOUND, "tender record not found", status_code=404)
    return _record_to_dict(row, include_brief=True)


async def _get_row(db: AsyncSession, public_id: str) -> TenderRecord:
    pid = (public_id or "").strip()
    if not pid:
        raise AppError(ErrorCode.BAD_REQUEST, "missing record id", status_code=400)
    row = (
        await db.execute(select(TenderRecord).where(TenderRecord.public_id == pid))
    ).scalar_one_or_none()
    if row is None:
        raise AppError(ErrorCode.NOT_FOUND, "tender record not found", status_code=404)
    return row


async def delete_record(db: AsyncSession, public_id: str) -> dict[str, object]:
    """删除生成记录，并尽量清理对应 Word / 资质 PDF 文件。"""
    row = await _get_row(db, public_id)
    out_dir = tenders_output_dir()
    removed: list[str] = []
    for name in (row.docx_file, row.pdf_file):
        if not name:
            continue
        path = (out_dir / str(name)).resolve()
        try:
            if path.is_file() and path.is_relative_to(out_dir.resolve()):
                path.unlink(missing_ok=True)
                removed.append(str(name))
        except OSError:
            pass
    payload = _record_to_dict(row)
    await db.delete(row)
    await db.commit()
    return {"deleted": True, "id": payload["id"], "removedFiles": removed}


async def regenerate_from_record(
    db: AsyncSession,
    public_id: str,
    *,
    user_id: int,
    username: str,
) -> dict[str, object]:
    """用记录里保存的表单 + 当前资料库附件重新生成一份 Word。"""
    row = await _get_row(db, public_id)
    if not isinstance(row.brief_json, dict) or not row.brief_json:
        raise AppError(ErrorCode.VALIDATION, "该记录没有保存表单，无法重新生成", status_code=422)
    try:
        brief = BidBrief.model_validate(row.brief_json)
    except Exception as exc:
        raise AppError(ErrorCode.VALIDATION, f"记录表单无法解析：{exc}", status_code=422) from exc
    return await create_record_from_generate(
        db,
        brief,
        user_id=user_id,
        username=username,
    )
