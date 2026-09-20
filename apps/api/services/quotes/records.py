"""AI 报价/造价/预算记录：入库、列表、详情、删除。"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.services.quotes.assemble import _dump_line
from api.services.quotes.contract import PURPOSE_STOP, default_rates_dict, normalize_purpose
from api.services.quotes.costing import apply_costing
from api.services.quotes.decide import build_schemes
from api.services.quotes.excel import write_quote_xlsx
from api.services.quotes.library import quotes_output_dir
from api.services.quotes.schema import GenerateQuoteIn, QuoteLineIn
from api.services.quotes.verify import apply_amount_fixes, verify_lines
from common.errors import AppError, ErrorCode
from common.times import to_epoch_ms
from db.models.quote import QuoteRecord
from db.models.user import User


def _lines_dump(lines: list[QuoteLineIn] | list[dict]) -> list[dict]:
    out: list[dict] = []
    for ln in lines:
        if isinstance(ln, QuoteLineIn):
            if not str(ln.name or "").strip():
                continue
            item = ln.model_dump()
            for key in ("qty", "unitPrice", "costPrice", "sellPrice", "amount"):
                item[key] = float(item.get(key) or 0)
            out.append(item)
            continue
        if not isinstance(ln, dict):
            continue
        if not str(ln.get("name") or "").strip():
            continue
        out.append(_dump_line(ln))
    return out


def _lines_load(raw) -> list[QuoteLineIn]:
    if not isinstance(raw, list):
        return []
    rows: list[QuoteLineIn] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            rows.append(QuoteLineIn.model_validate(item))
        except Exception:
            continue
    return rows


def _xlsx_ok(name: str) -> bool:
    file_name = (name or "").strip()
    if not file_name:
        return False
    return (quotes_output_dir() / file_name).is_file()


def _unlink_xlsx(name: str) -> None:
    file_name = (name or "").strip()
    if not file_name:
        return
    path = quotes_output_dir() / file_name
    try:
        if path.is_file():
            path.unlink()
    except OSError:
        pass


def record_to_dict(row: QuoteRecord, *, include_lines: bool = False) -> dict[str, object]:
    data: dict[str, object] = {
        "id": row.public_id,
        "purpose": getattr(row, "purpose", None) or "quote",
        "stage": getattr(row, "stage", None) or "quoted",
        "projectName": row.project_name or "",
        "note": row.note or "",
        "location": getattr(row, "location", None) or "",
        "durationDays": getattr(row, "duration_days", None),
        "bidCeiling": getattr(row, "bid_ceiling", None),
        "competition": getattr(row, "competition", None) or "balanced",
        "targetMargin": getattr(row, "target_margin", None),
        "baseId": row.base_id or "",
        "baseName": row.base_name or "",
        "taxRate": float(row.tax_rate or 0),
        "totalExTax": float(row.total_ex_tax or 0),
        "totalIncTax": float(row.total_inc_tax or 0),
        "unmatched": int(row.unmatched or 0),
        "lineCount": int(row.line_count or 0),
        "xlsxFile": row.xlsx_file or "",
        "downloadName": row.download_name or "",
        "xlsxAvailable": _xlsx_ok(row.xlsx_file),
        "username": row.username or "",
        "createdAt": to_epoch_ms(row.created_at),
        "rates": row.rates_json if isinstance(getattr(row, "rates_json", None), dict) else None,
        "verify": row.verify_json if isinstance(getattr(row, "verify_json", None), dict) else None,
        "schemes": row.schemes_json if isinstance(getattr(row, "schemes_json", None), list) else None,
        "instruction": getattr(row, "instruction_text", None) or "",
    }
    if include_lines:
        data["lines"] = row.lines_json if isinstance(row.lines_json, list) else []
    return data


def _line_dicts(body_lines: list[QuoteLineIn]) -> list[dict[str, Any]]:
    return _lines_dump(body_lines)


async def create_from_generate(
    db: AsyncSession,
    body: GenerateQuoteIn,
    *,
    user_id: int,
    username: str,
) -> dict[str, object]:
    purpose = normalize_purpose(body.purpose)
    stage = PURPOSE_STOP.get(purpose, "quoted")
    line_dicts = _line_dicts(body.lines)
    if not line_dicts:
        raise AppError(ErrorCode.VALIDATION, "没有明细行，无法生成", status_code=422)

    verify = verify_lines(line_dicts)
    if body.applyVerifyFixes:
        line_dicts = apply_amount_fixes(line_dicts, verify)
        verify = verify_lines(line_dicts)

    rates_raw = body.rates.model_dump() if body.rates else None
    if rates_raw is None:
        rates_raw = default_rates_dict()
        rates_raw["taxRate"] = float(body.taxRate or 0.13)
    else:
        rates_raw["taxRate"] = float(body.taxRate if body.taxRate is not None else rates_raw.get("taxRate", 0.13))

    costed, summary = apply_costing(line_dicts, rates_raw)
    tax_rate = Decimal(str(summary.get("taxRate") or body.taxRate or 0.13))

    schemes: list[dict] = []
    instruction = ""
    risks: list[str] = []
    if purpose == "quote":
        ceiling = body.bidCeiling
        margin = body.targetMargin
        schemes, instruction, risks = build_schemes(
            cost_ex=Decimal(str(summary["costExTax"])),
            quote_ex=Decimal(str(summary["quoteExTax"])),
            bid_ceiling=ceiling,
            competition=body.competition or "balanced",
            target_margin=margin,
            project_name=body.projectName,
            location=body.location or "",
            duration_days=body.durationDays,
        )
        if risks:
            verify = dict(verify)
            issues = list(verify.get("issues") or [])
            for msg in risks:
                issues.append({"code": "scheme_risk", "level": "warn", "row": 0, "message": msg})
            verify["issues"] = issues
            verify["warnCount"] = sum(1 for x in issues if x.get("level") == "warn")
    elif purpose in {"cost", "budget"}:
        loc = (body.location or "").strip() or "未注明"
        days = f"{int(body.durationDays)} 天" if body.durationDays else "未注明"
        title = (body.projectName or "").strip() or "本项目"
        if purpose == "cost":
            instruction = (
                f"「{title}」造价测算说明\n"
                f"地点 {loc}；工期 {days}。\n"
                f"材料成本 {summary['materialCost']:.2f} 元，测算造价（不含税）"
                f"{summary['costExTax']:.2f} 元。数字以表为准，请人工复核。"
            )
        else:
            instruction = (
                f"「{title}」控制预算说明\n"
                f"地点 {loc}；工期 {days}。\n"
                f"测算造价 {summary['costExTax']:.2f} 元，控制预算（不含税）"
                f"{summary['budgetExTax']:.2f} 元（含不可预见费与预算系数）。请人工复核。"
            )

    days_i = int(body.durationDays) if body.durationDays else None

    export_lines = [QuoteLineIn.model_validate(r) for r in costed]
    path, download_name, total, inc = write_quote_xlsx(
        project_name=body.projectName,
        note=body.note,
        tax_rate=tax_rate,
        lines=export_lines,
        purpose=purpose,
        cost_summary=summary,
        schemes=schemes or None,
        instruction=instruction,
        verify=verify,
    )
    lines = _lines_dump(costed)
    unmatched = sum(
        1
        for ln in lines
        if float(ln.get("unitPrice") or 0) <= 0 and float(ln.get("costPrice") or 0) <= 0
    )

    row = QuoteRecord(
        public_id=uuid4().hex[:16],
        user_id=int(user_id),
        username=(username or "").strip()[:64],
        purpose=purpose,
        stage=stage,
        project_name=(body.projectName or "").strip()[:256],
        note=(body.note or "").strip()[:2000],
        location=(body.location or "").strip()[:128],
        duration_days=days_i,
        bid_ceiling=float(body.bidCeiling) if body.bidCeiling is not None else None,
        competition=(body.competition or "balanced")[:32],
        target_margin=float(body.targetMargin) if body.targetMargin is not None else None,
        base_id=(body.baseId or "").strip()[:32],
        base_name=(body.baseName or "").strip()[:128],
        tax_rate=float(tax_rate),
        total_ex_tax=float(total),
        total_inc_tax=float(inc),
        unmatched=unmatched,
        line_count=len(lines),
        xlsx_file=path.name,
        download_name=download_name,
        lines_json=lines,
        rates_json=summary.get("rates") or rates_raw,
        verify_json=verify,
        schemes_json=schemes or None,
        instruction_text=instruction or None,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    out = record_to_dict(row)
    out["costSummary"] = summary
    out["xlsxFile"] = path.name
    out["downloadName"] = download_name
    out["totalExTax"] = float(total)
    out["totalIncTax"] = float(inc)
    out["unmatched"] = unmatched
    out["lineCount"] = len(lines)
    return out


async def list_records(
    db: AsyncSession,
    *,
    user_id: int,
    q: str | None = None,
    purpose: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, object]:
    clauses = [QuoteRecord.user_id == int(user_id)]
    if purpose:
        clauses.append(QuoteRecord.purpose == normalize_purpose(purpose))
    keyword = (q or "").strip()
    if keyword:
        like = f"%{keyword}%"
        clauses.append(
            or_(
                QuoteRecord.project_name.like(like),
                QuoteRecord.username.like(like),
                QuoteRecord.base_name.like(like),
            )
        )
    cond = and_(*clauses)
    total = int((await db.execute(select(func.count()).select_from(QuoteRecord).where(cond))).scalar() or 0)
    rows = (
        (
            await db.execute(
                select(QuoteRecord)
                .where(cond)
                .order_by(QuoteRecord.id.desc())
                .offset(max(0, offset))
                .limit(max(1, min(limit, 200)))
            )
        )
        .scalars()
        .all()
    )
    return {"total": total, "items": [record_to_dict(row) for row in rows]}


async def _get_row(db: AsyncSession, public_id: str) -> QuoteRecord:
    pid = (public_id or "").strip()
    row = (
        await db.execute(select(QuoteRecord).where(QuoteRecord.public_id == pid))
    ).scalar_one_or_none()
    if row is None:
        raise AppError(ErrorCode.NOT_FOUND, "报价记录不存在", status_code=404)
    return row


def _assert_owner(row: QuoteRecord, user: User, *, admin: bool) -> None:
    if admin:
        return
    if int(row.user_id) != int(user.id):
        raise AppError(ErrorCode.FORBIDDEN, "只能操作本人的报价记录", status_code=403)


async def get_record(
    db: AsyncSession,
    public_id: str,
    *,
    user: User,
    admin: bool,
) -> dict[str, object]:
    row = await _get_row(db, public_id)
    _assert_owner(row, user, admin=admin)
    return record_to_dict(row, include_lines=True)


async def load_owned(
    db: AsyncSession,
    *,
    user_id: int,
    public_ids: list[str],
) -> list[QuoteRecord]:
    ids = [str(x).strip() for x in public_ids if str(x).strip()]
    if not ids:
        return []
    uid = int(user_id or 0)
    if uid <= 0:
        raise AppError(ErrorCode.FORBIDDEN, "只能引用本人的报价记录", status_code=403)
    rows = (
        (
            await db.execute(
                select(QuoteRecord).where(
                    QuoteRecord.public_id.in_(ids),
                    QuoteRecord.user_id == uid,
                )
            )
        )
        .scalars()
        .all()
    )
    by_id = {str(r.public_id): r for r in rows}
    missing = [pid for pid in ids if pid not in by_id]
    if missing:
        raise AppError(
            ErrorCode.NOT_FOUND,
            f"报价记录不存在或无权引用（{len(missing)} 条）",
            status_code=404,
        )
    return [by_id[pid] for pid in ids]


async def delete_record(
    db: AsyncSession,
    public_id: str,
    *,
    user: User,
    admin: bool,
) -> dict[str, object]:
    row = await _get_row(db, public_id)
    _assert_owner(row, user, admin=admin)
    _unlink_xlsx(row.xlsx_file)
    payload = record_to_dict(row)
    await db.delete(row)
    await db.commit()
    return {"deleted": True, "id": payload["id"]}


async def clear_mine(
    db: AsyncSession,
    *,
    user: User,
    purpose: str | None = None,
) -> dict[str, object]:
    clauses = [QuoteRecord.user_id == int(user.id)]
    if purpose:
        clauses.append(QuoteRecord.purpose == normalize_purpose(purpose))
    rows = (await db.execute(select(QuoteRecord).where(and_(*clauses)))).scalars().all()
    ids: list[str] = []
    for row in rows:
        _unlink_xlsx(row.xlsx_file)
        ids.append(str(row.public_id))
        await db.delete(row)
    await db.commit()
    return {"deleted": len(ids), "ids": ids}


async def rewrite_xlsx(
    db: AsyncSession,
    public_id: str,
    *,
    user: User,
    admin: bool,
) -> dict[str, object]:
    row = await _get_row(db, public_id)
    _assert_owner(row, user, admin=admin)
    lines = _lines_load(row.lines_json)
    if not lines:
        raise AppError(ErrorCode.VALIDATION, "该记录没有保存明细，无法重新导出", status_code=422)
    purpose = normalize_purpose(getattr(row, "purpose", None) or "quote")
    old = row.xlsx_file
    path, download_name, total, inc = write_quote_xlsx(
        project_name=row.project_name,
        note=row.note,
        tax_rate=Decimal(str(row.tax_rate or 0)),
        lines=lines,
        purpose=purpose,
        cost_summary=None,
        schemes=row.schemes_json if isinstance(row.schemes_json, list) else None,
        instruction=row.instruction_text or "",
        verify=row.verify_json if isinstance(row.verify_json, dict) else None,
    )
    if old and old != path.name:
        _unlink_xlsx(old)
    row.xlsx_file = path.name
    row.download_name = download_name
    row.total_ex_tax = float(total)
    row.total_inc_tax = float(inc)
    row.line_count = len(lines)
    row.unmatched = sum(1 for ln in lines if float(ln.unitPrice or 0) <= 0)
    await db.commit()
    await db.refresh(row)
    return record_to_dict(row)
