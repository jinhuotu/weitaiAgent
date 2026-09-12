"""投标文件生成记录：入库、列表、详情、审批。"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.services.tenders.assets import tenders_output_dir
from api.services.tenders.generate import generate_bid
from api.services.tenders.schema import BidBrief, PlaceholderItem
from common.errors import AppError, ErrorCode
from common.times import to_epoch_ms
from db.models.tender import TenderApprovalLog, TenderRecord
from db.models.user import User


STATUS_PROCESSING = "processing"
STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_SUBMITTED = "submitted"
STATUS_WON = "won"
STATUS_LOST = "lost"
TASK_STATUSES = frozenset(
    {
        STATUS_PROCESSING,
        STATUS_PENDING,
        STATUS_APPROVED,
        STATUS_SUBMITTED,
        STATUS_WON,
        STATUS_LOST,
    }
)
LOCKED_STATUSES = frozenset(
    {STATUS_PENDING, STATUS_APPROVED, STATUS_SUBMITTED, STATUS_WON, STATUS_LOST}
)
APPROVAL_STEPS = (
    "提交申请",
    "部门经理审批",
    "总经理审批",
    "财务审核",
    "完成",
)
REVIEW_STEPS = ("部门经理审批", "总经理审批", "财务审核")
ACTION_SUBMIT = "submit"
ACTION_PASS = "pass"
ACTION_REJECT = "reject"
ACTION_MARK = "mark"


def infer_project_type(name: str) -> str:
    text = name or ""
    if any(key in text for key in ("数据中心", "机房")):
        return "数据中心"
    if any(key in text for key in ("安防", "监控", "公安")):
        return "安防系统"
    if any(key in text for key in ("园区", "智慧城市", "交通", "综合体")):
        return "智慧园区"
    return "弱电工程"


def deadline_from_brief(brief: BidBrief | dict | None) -> str:
    if brief is None:
        return ""
    raw = brief.bidDate if isinstance(brief, BidBrief) else str(brief.get("bidDate") or "")
    return (raw or "").strip()[:16]


def workflow_locked(status: str | None) -> bool:
    return (status or STATUS_PROCESSING) in LOCKED_STATUSES


def next_step_on_pass(current_step: str) -> tuple[str, str]:
    """返回 (status, current_step)。最后一审通过则 approved + 完成。"""
    step = (current_step or "").strip() or REVIEW_STEPS[0]
    if step not in REVIEW_STEPS:
        step = REVIEW_STEPS[0]
    idx = REVIEW_STEPS.index(step)
    if idx >= len(REVIEW_STEPS) - 1:
        return STATUS_APPROVED, APPROVAL_STEPS[-1]
    return STATUS_PENDING, REVIEW_STEPS[idx + 1]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _record_to_dict(
    row: TenderRecord,
    *,
    include_brief: bool = False,
    extra: dict[str, object] | None = None,
) -> dict[str, object]:
    docx_ok = (tenders_output_dir() / row.docx_file).is_file()
    pdf_ok = bool(row.pdf_file) and (tenders_output_dir() / str(row.pdf_file)).is_file()
    status = (getattr(row, "status", None) or STATUS_PROCESSING).strip() or STATUS_PROCESSING
    submitted_at = getattr(row, "submitted_at", None)
    decided_at = getattr(row, "decided_at", None)
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
        "status": status,
        "deadline": getattr(row, "deadline", None) or "",
        "projectType": getattr(row, "project_type", None) or "",
        "currentStep": getattr(row, "current_step", None) or "",
        "submittedAt": to_epoch_ms(submitted_at) if submitted_at else None,
        "decidedAt": to_epoch_ms(decided_at) if decided_at else None,
        "workflowLocked": workflow_locked(status),
        "userId": int(row.user_id or 0),
    }
    if include_brief and isinstance(row.brief_json, dict):
        data["brief"] = row.brief_json
    if extra:
        data.update(extra)
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


async def _prepare_and_generate(
    db: AsyncSession,
    brief: BidBrief,
) -> tuple[dict[str, object], dict[str, object]]:
    """跑生成管道，返回 (generated, attachment_match)。会就地补全 brief 的槽位字段。"""
    import time

    from api.services.tenders.extract import merge_performance_lines
    from api.services.tenders.library_kb import (
        catalog_placeholders,
        count_uncached_performance_files,
        filter_performance_attachments,
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
    lib_perf = await performance_from_library(db, extract_missing=False)
    pending_perf = await count_uncached_performance_files(db)
    if lib_perf:
        brief.performanceLines = merge_performance_lines(
            brief.performanceLines,
            lib_perf,
            requirement=brief.performanceRequirement,
            preserve_flags=True,
        )
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
    from api.services.tenders.performance import bid_performance_lines

    if "perf" in media:
        media["perf"] = await filter_performance_attachments(
            db, media["perf"], bid_performance_lines(brief.performanceLines)
        )
        if not media["perf"]:
            media.pop("perf", None)
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
    return generated, attachment_match


def _apply_generated_files(row: TenderRecord, brief: BidBrief, generated: dict[str, object]) -> None:
    row.project_name = (brief.projectName or "").strip()[:256]
    row.tenderer = (brief.tenderer or "").strip()[:256]
    row.bid_price_yuan = float(brief.bidPriceYuan or 0)
    row.legal_person_name = (brief.legalPersonName or "").strip()[:64]
    row.docx_file = str(generated["docxFile"])
    row.pdf_file = generated.get("pdfFile") or None
    row.download_name = str(generated.get("downloadName") or "")
    row.pdf_download_name = generated.get("pdfDownloadName") or None
    row.warnings = list(generated.get("warnings") or [])
    row.brief_json = brief.model_dump(mode="json")
    row.deadline = deadline_from_brief(brief)
    if not (row.project_type or "").strip():
        row.project_type = infer_project_type(row.project_name)


def _unlink_output(name: str | None) -> None:
    if not name:
        return
    out_dir = tenders_output_dir()
    path = (out_dir / str(name)).resolve()
    try:
        if path.is_file() and path.is_relative_to(out_dir.resolve()):
            path.unlink(missing_ok=True)
    except OSError:
        pass


async def create_record_from_generate(
    db: AsyncSession,
    brief: BidBrief,
    *,
    user_id: int,
    username: str,
) -> dict[str, object]:
    """生成 Word 并写入 tender_records（新任务，状态编制中）。"""
    generated, attachment_match = await _prepare_and_generate(db, brief)
    row = TenderRecord(
        public_id=uuid4().hex[:16],
        user_id=user_id,
        username=(username or "").strip()[:64],
        status=STATUS_PROCESSING,
        current_step="",
        project_type=infer_project_type((brief.projectName or "").strip()),
        deadline=deadline_from_brief(brief),
        docx_file=str(generated["docxFile"]),
        download_name=str(generated.get("downloadName") or ""),
    )
    _apply_generated_files(row, brief, generated)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    out = _record_to_dict(row)
    out["warnings"] = generated.get("warnings") or []
    out["defaultsUsed"] = generated.get("defaultsUsed")
    out["attachmentMatch"] = attachment_match
    return out


def _filter_clause(
    *,
    q: str | None,
    status: str | None,
    owner: str | None,
    user_id: int | None,
    project_type: str | None,
):
    clauses = []
    keyword = (q or "").strip()
    if keyword:
        like = f"%{keyword}%"
        clauses.append(
            or_(
                TenderRecord.project_name.like(like),
                TenderRecord.tenderer.like(like),
                TenderRecord.username.like(like),
            )
        )
    if status and status in TASK_STATUSES:
        clauses.append(TenderRecord.status == status)
    if owner and owner.strip():
        clauses.append(TenderRecord.username == owner.strip())
    if user_id is not None:
        clauses.append(TenderRecord.user_id == int(user_id))
    if project_type and project_type.strip():
        clauses.append(TenderRecord.project_type == project_type.strip())
    if not clauses:
        return None
    return and_(*clauses) if len(clauses) > 1 else clauses[0]


async def list_records(
    db: AsyncSession,
    *,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
    status: str | None = None,
    owner: str | None = None,
    user_id: int | None = None,
    project_type: str | None = None,
    record_ids: list[int] | None = None,
    submitted_only: bool = False,
) -> dict[str, object]:
    clause = _filter_clause(
        q=q, status=status, owner=owner, user_id=user_id, project_type=project_type
    )
    base = select(func.count()).select_from(TenderRecord)
    stmt = select(TenderRecord).order_by(TenderRecord.id.desc())
    if clause is not None:
        base = base.where(clause)
        stmt = stmt.where(clause)
    if submitted_only:
        base = base.where(TenderRecord.submitted_at.is_not(None))
        stmt = stmt.where(TenderRecord.submitted_at.is_not(None))
    if record_ids is not None:
        if not record_ids:
            return {"total": 0, "items": []}
        base = base.where(TenderRecord.id.in_(record_ids))
        stmt = stmt.where(TenderRecord.id.in_(record_ids))
    total = int((await db.execute(base)).scalar_one() or 0)
    rows = (
        await db.execute(stmt.offset(max(0, offset)).limit(max(1, min(limit, 200))))
    ).scalars().all()
    extras = await _list_log_extras(db, [int(r.id) for r in rows])
    return {
        "total": total,
        "items": [_record_to_dict(r, extra=extras.get(int(r.id))) for r in rows],
    }


async def _list_log_extras(
    db: AsyncSession, record_ids: list[int]
) -> dict[int, dict[str, object]]:
    if not record_ids:
        return {}
    rows = (
        await db.execute(
            select(TenderApprovalLog)
            .where(TenderApprovalLog.record_id.in_(record_ids))
            .order_by(TenderApprovalLog.id.desc())
        )
    ).scalars().all()
    extras: dict[int, dict[str, object]] = {}
    for log in rows:
        rid = int(log.record_id)
        if rid in extras:
            continue
        extras[rid] = {
            "lastAction": log.action,
            "lastComment": log.comment or "",
            "lastActor": log.username or "",
        }
    return extras


async def get_record(db: AsyncSession, public_id: str) -> dict[str, object]:
    row = await _get_row(db, public_id)
    rid = int(row.id) if getattr(row, "id", None) is not None else 0
    extras = await _list_log_extras(db, [rid] if rid else [])
    log_rows = list(getattr(row, "approval_logs", None) or [])
    logs = [
        {
            "id": int(item.id),
            "action": item.action,
            "step": item.step,
            "comment": item.comment or "",
            "username": item.username or "",
            "createdAt": to_epoch_ms(item.created_at),
        }
        for item in sorted(log_rows, key=lambda x: int(x.id or 0))
    ]
    extra = dict(extras.get(rid) or {})
    extra["approvalLogs"] = logs
    return _record_to_dict(row, include_brief=True, extra=extra)


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


def _assert_owner_or_admin(row: TenderRecord, user: User, *, admin: bool) -> None:
    if admin:
        return
    if int(row.user_id) != int(user.id):
        raise AppError(ErrorCode.FORBIDDEN, "只能操作本人的投标任务", status_code=403)


def _purge_record_files(row: TenderRecord) -> list[str]:
    removed: list[str] = []
    for name in (row.docx_file, row.pdf_file):
        if name:
            _unlink_output(str(name))
            removed.append(str(name))
    from api.services.tenders.qa import unlink_qa_report

    unlink_qa_report(row.public_id)
    return removed


async def delete_record(
    db: AsyncSession,
    public_id: str,
    *,
    user: User,
    admin: bool,
) -> dict[str, object]:
    """删除生成记录，并尽量清理对应 Word / 资质 PDF 文件。"""
    row = await _get_row(db, public_id)
    if admin:
        pass
    else:
        _assert_owner_or_admin(row, user, admin=False)
        if workflow_locked(row.status):
            raise AppError(ErrorCode.VALIDATION, "审批中或已定稿的任务不能删除", status_code=422)
    removed = _purge_record_files(row)
    payload = _record_to_dict(row)
    await db.delete(row)
    await db.commit()
    return {"deleted": True, "id": payload["id"], "removedFiles": removed}


async def clear_mine_records(
    db: AsyncSession,
    *,
    user: User,
    admin: bool,
) -> dict[str, object]:
    """清空当前用户自己的生成记录。管理员可删本人名下含审批中的任务。"""
    rows = (
        (await db.execute(select(TenderRecord).where(TenderRecord.user_id == int(user.id))))
        .scalars()
        .all()
    )
    deleted = 0
    skipped = 0
    removed_files: list[str] = []
    ids: list[str] = []
    for row in rows:
        if not admin and workflow_locked(row.status):
            skipped += 1
            continue
        removed_files.extend(_purge_record_files(row))
        ids.append(str(row.public_id))
        await db.delete(row)
        deleted += 1
    await db.commit()
    return {
        "deleted": deleted,
        "skipped": skipped,
        "ids": ids,
        "removedFiles": removed_files,
    }


async def regenerate_from_record(
    db: AsyncSession,
    public_id: str,
    *,
    user: User,
    admin: bool,
    brief: BidBrief | None = None,
) -> dict[str, object]:
    """用当前表单（或记录快照）覆盖同一条记录的 Word，不改工作流状态。"""
    row = await _get_row(db, public_id)
    _assert_owner_or_admin(row, user, admin=admin)
    if workflow_locked(row.status):
        raise AppError(ErrorCode.VALIDATION, "当前状态不可重新生成，请先驳回或解锁", status_code=422)
    if brief is None:
        if not isinstance(row.brief_json, dict) or not row.brief_json:
            raise AppError(ErrorCode.VALIDATION, "该记录没有保存表单，无法重新生成", status_code=422)
        try:
            brief = BidBrief.model_validate(row.brief_json)
        except Exception as exc:
            raise AppError(ErrorCode.VALIDATION, f"记录表单无法解析：{exc}", status_code=422) from exc
    old_docx, old_pdf = row.docx_file, row.pdf_file
    generated, attachment_match = await _prepare_and_generate(db, brief)
    _apply_generated_files(row, brief, generated)
    await db.commit()
    await db.refresh(row)
    if old_docx and old_docx != row.docx_file:
        _unlink_output(old_docx)
    if old_pdf and old_pdf != row.pdf_file:
        _unlink_output(str(old_pdf))
    from api.services.tenders.qa import unlink_qa_report

    unlink_qa_report(row.public_id)
    out = _record_to_dict(row)
    out["warnings"] = generated.get("warnings") or []
    out["defaultsUsed"] = generated.get("defaultsUsed")
    out["attachmentMatch"] = attachment_match
    return out


async def _add_log(
    db: AsyncSession,
    row: TenderRecord,
    *,
    user: User,
    action: str,
    step: str,
    comment: str = "",
) -> None:
    db.add(
        TenderApprovalLog(
            record_id=int(row.id),
            user_id=int(user.id),
            username=(user.username or "").strip()[:64],
            action=action,
            step=(step or "")[:32],
            comment=(comment or "").strip()[:2000],
        )
    )


async def submit_for_approval(
    db: AsyncSession,
    public_id: str,
    *,
    user: User,
    admin: bool,
) -> dict[str, object]:
    row = await _get_row(db, public_id)
    _assert_owner_or_admin(row, user, admin=admin)
    if (row.status or STATUS_PROCESSING) != STATUS_PROCESSING:
        raise AppError(ErrorCode.VALIDATION, "只有编制中的任务可以提交审批", status_code=422)
    if not (tenders_output_dir() / row.docx_file).is_file():
        raise AppError(ErrorCode.VALIDATION, "Word 文件缺失，无法提交审批", status_code=422)
    row.status = STATUS_PENDING
    row.current_step = REVIEW_STEPS[0]
    row.submitted_at = _now()
    await _add_log(db, row, user=user, action=ACTION_SUBMIT, step=row.current_step, comment="")
    await db.commit()
    await db.refresh(row)
    extras = await _list_log_extras(db, [int(row.id)])
    return _record_to_dict(row, extra=extras.get(int(row.id)))


async def decide_approval(
    db: AsyncSession,
    public_id: str,
    *,
    user: User,
    passed: bool,
    comment: str = "",
) -> dict[str, object]:
    row = await _get_row(db, public_id)
    if (row.status or "") != STATUS_PENDING:
        raise AppError(ErrorCode.VALIDATION, "当前任务不在待审批状态", status_code=422)
    now = _now()
    if passed:
        new_status, new_step = next_step_on_pass(row.current_step)
        row.status = new_status
        row.current_step = new_step
        if new_status == STATUS_APPROVED:
            row.decided_at = now
        await _add_log(
            db,
            row,
            user=user,
            action=ACTION_PASS,
            step=new_step,
            comment=comment,
        )
    else:
        row.status = STATUS_PROCESSING
        row.current_step = APPROVAL_STEPS[0]
        row.decided_at = now
        await _add_log(
            db,
            row,
            user=user,
            action=ACTION_REJECT,
            step=row.current_step,
            comment=comment or "驳回",
        )
    await db.commit()
    await db.refresh(row)
    extras = await _list_log_extras(db, [int(row.id)])
    return _record_to_dict(row, extra=extras.get(int(row.id)))


async def mark_result(
    db: AsyncSession,
    public_id: str,
    *,
    user: User,
    admin: bool,
    status: str,
) -> dict[str, object]:
    target = (status or "").strip()
    if target not in {STATUS_SUBMITTED, STATUS_WON, STATUS_LOST}:
        raise AppError(ErrorCode.VALIDATION, "结果只能是已提交 / 已中标 / 已失败", status_code=422)
    row = await _get_row(db, public_id)
    _assert_owner_or_admin(row, user, admin=admin)
    current = row.status or STATUS_PROCESSING
    if target == STATUS_SUBMITTED and current != STATUS_APPROVED:
        raise AppError(ErrorCode.VALIDATION, "审批通过后才能标记已提交", status_code=422)
    if target in {STATUS_WON, STATUS_LOST} and current not in {STATUS_APPROVED, STATUS_SUBMITTED}:
        raise AppError(ErrorCode.VALIDATION, "审批通过后才能标记中标结果", status_code=422)
    row.status = target
    row.current_step = APPROVAL_STEPS[-1]
    await _add_log(db, row, user=user, action=ACTION_MARK, step=row.current_step, comment=target)
    await db.commit()
    await db.refresh(row)
    extras = await _list_log_extras(db, [int(row.id)])
    return _record_to_dict(row, extra=extras.get(int(row.id)))


async def record_ids_for_actor(
    db: AsyncSession,
    *,
    user_id: int,
    actions: tuple[str, ...],
) -> list[int]:
    rows = (
        await db.execute(
            select(TenderApprovalLog.record_id)
            .where(
                TenderApprovalLog.user_id == int(user_id),
                TenderApprovalLog.action.in_(actions),
            )
            .distinct()
        )
    ).all()
    return [int(r[0]) for r in rows]


async def inspect_record_qa(db: AsyncSession, public_id: str) -> dict[str, object]:
    """对照邀请书对已生成 Word 做 AI 质检。"""
    from api.services.tenders.qa import inspect_bid

    row = await _get_row(db, public_id)
    if not (row.docx_file or "").strip():
        raise AppError(ErrorCode.VALIDATION, "该记录没有 Word 文件，无法质检", status_code=422)
    if not isinstance(row.brief_json, dict) or not row.brief_json:
        raise AppError(ErrorCode.VALIDATION, "该记录没有保存表单，无法质检", status_code=422)
    try:
        brief = BidBrief.model_validate(row.brief_json)
    except Exception as exc:
        raise AppError(ErrorCode.VALIDATION, f"记录表单无法解析：{exc}", status_code=422) from exc
    return await inspect_bid(
        db,
        public_id=row.public_id,
        brief=brief,
        docx_file=str(row.docx_file),
    )


def get_saved_qa(public_id: str, *, docx_file: str | None = None) -> dict[str, object]:
    from api.services.tenders.qa import load_qa_report

    report = load_qa_report(public_id)
    if not report:
        return {"report": None}
    stale = bool(
        docx_file and report.get("docxFile") and str(report.get("docxFile")) != str(docx_file)
    )
    report["stale"] = stale
    return {"report": report}


async def get_record_qa(db: AsyncSession, public_id: str) -> dict[str, object]:
    row = await _get_row(db, public_id)
    return get_saved_qa(row.public_id, docx_file=row.docx_file)
