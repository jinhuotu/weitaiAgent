"""投标文件生成记录：入库、列表、详情。"""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.services.tenders.assets import tenders_output_dir
from api.services.tenders.generate import generate_bid
from api.services.tenders.schema import BidBrief
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


async def create_record_from_generate(
    db: AsyncSession,
    brief: BidBrief,
    *,
    user_id: int,
    username: str,
) -> dict[str, object]:
    """生成 Word 并写入 tender_records。"""
    generated = generate_bid(brief)
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
    if not (tenders_output_dir() / row.docx_file).is_file():
        raise AppError(
            ErrorCode.NOT_FOUND,
            "记录对应的 Word 文件已丢失，无法预览或下载",
            status_code=404,
        )
    return _record_to_dict(row, include_brief=True)
