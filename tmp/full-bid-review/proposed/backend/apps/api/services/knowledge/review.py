"""资料审核：通过后才写入 Qdrant；驳回删除向量。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.services.knowledge.bases import get_base_by_public_id
from api.services.knowledge.ingest import (
    _vectorize_document,
    read_extracted_text,
    to_kb_item,
)
from api.services.knowledge.qdrant_store import get_qdrant_store
from common.errors import AppError, ErrorCode
from common.times import to_epoch_ms
from db.models.knowledge import KnowledgeDocument, KnowledgeDocumentReview

REVIEW_PENDING = "pending"
REVIEW_APPROVED = "approved"
REVIEW_REJECTED = "rejected"


async def list_document_reviews(
    db: AsyncSession, *, base_public_id: str, doc_public_id: str
) -> list[dict[str, Any]]:
    base = await get_base_by_public_id(db, base_public_id)
    doc = await _get_doc(db, base_id=base.id, public_id=doc_public_id)
    result = await db.execute(
        select(KnowledgeDocumentReview)
        .where(KnowledgeDocumentReview.document_id == doc.id)
        .order_by(KnowledgeDocumentReview.created_at.desc())
    )
    out: list[dict[str, Any]] = []
    for row in result.scalars().all():
        out.append(
            {
                "id": row.id,
                "action": row.action,
                "comment": row.comment,
                "actorId": row.actor_id,
                "createdAt": to_epoch_ms(row.created_at),
            }
        )
    return out


async def review_document(
    db: AsyncSession,
    *,
    base_public_id: str,
    doc_public_id: str,
    action: str,
    comment: str | None,
    actor_id: int | None,
) -> dict[str, Any]:
    act = (action or "").strip().lower()
    if act not in {REVIEW_APPROVED, "approve", REVIEW_REJECTED, "reject"}:
        raise AppError(ErrorCode.VALIDATION, "action 必须是 approve 或 reject", status_code=422)
    approve = act in {REVIEW_APPROVED, "approve"}
    base = await get_base_by_public_id(db, base_public_id)
    doc = await _get_doc(db, base_id=base.id, public_id=doc_public_id)
    if doc.status == "parsing":
        raise AppError(ErrorCode.CONFLICT, "文档正在解析，请稍候", status_code=409)
    if doc.status == "failed":
        raise AppError(ErrorCode.CONFLICT, "解析失败的资料请先重试", status_code=409)
    if (doc.kind or "") == "drawing":
        doc.review_status = REVIEW_APPROVED
        doc.review_comment = (comment or "").strip() or None
        doc.reviewed_by = actor_id
        doc.reviewed_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(doc)
        setattr(doc, "_base_public_id", base.public_id)
        return to_kb_item(doc)

    note = (comment or "").strip() or None
    db.add(
        KnowledgeDocumentReview(
            document_id=doc.id,
            action="approve" if approve else "reject",
            comment=note,
            actor_id=actor_id,
        )
    )
    doc.review_comment = note
    doc.reviewed_by = actor_id
    doc.reviewed_at = datetime.now(timezone.utc)

    if approve:
        text = read_extracted_text(base.public_id, doc.public_id) or (doc.summary or "")
        await _vectorize_document(db, doc, text)
        doc.review_status = REVIEW_APPROVED
    else:
        try:
            get_qdrant_store().delete_by_doc_id(doc.public_id)
        except Exception:  # noqa: BLE001
            pass
        doc.review_status = REVIEW_REJECTED
        doc.chunk_count = 0

    await db.commit()
    await db.refresh(doc)
    setattr(doc, "_base_public_id", base.public_id)
    return to_kb_item(doc)


async def reindex_approved_documents(db: AsyncSession) -> dict[str, Any]:
    """重建 Qdrant 后把已通过资料重新向量化。"""
    result = await db.execute(
        select(KnowledgeDocument).where(
            KnowledgeDocument.status == "ready",
            KnowledgeDocument.review_status == REVIEW_APPROVED,
            KnowledgeDocument.kind != "drawing",
        )
    )
    docs = list(result.scalars().all())
    ok = 0
    failed = 0
    from db.models.knowledge import KnowledgeBase

    bases: dict[int, KnowledgeBase] = {}
    for doc in docs:
        if doc.base_id not in bases:
            bases[doc.base_id] = (
                await db.execute(select(KnowledgeBase).where(KnowledgeBase.id == doc.base_id))
            ).scalar_one()
        base = bases[doc.base_id]
        text = read_extracted_text(base.public_id, doc.public_id) or (doc.summary or "")
        try:
            await _vectorize_document(db, doc, text)
            ok += 1
        except Exception:  # noqa: BLE001
            failed += 1
    await db.commit()
    return {"reindexed": ok, "failed": failed, "total": len(docs)}


async def _get_doc(db: AsyncSession, *, base_id: int, public_id: str) -> KnowledgeDocument:
    result = await db.execute(
        select(KnowledgeDocument).where(
            KnowledgeDocument.base_id == base_id,
            KnowledgeDocument.public_id == public_id,
        )
    )
    doc = result.scalar_one_or_none()
    if doc is None:
        raise AppError(ErrorCode.NOT_FOUND, "document not found", status_code=404)
    return doc
