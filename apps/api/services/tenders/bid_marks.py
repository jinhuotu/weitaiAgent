"""历史标书按章标好/坏。人手标记不被自动初标盖掉。"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from common.errors import AppError, ErrorCode
from db.models.tender import TenderApprovalLog, TenderBidMark, TenderRecord

PROBLEM_TAGS = (
    "报价",
    "业绩",
    "工期",
    "资质",
    "签章",
    "格式",
    "法人",
    "付款",
    "否决",
    "其他",
)

_RULES = (
    ("报价", ("报价", "总价", "分项")),
    ("业绩", ("业绩", "合同额")),
    ("工期", ("工期", "交货")),
    ("资质", ("资质", "证书", "执照")),
    ("签章", ("签章", "盖章", "签字")),
    ("格式", ("格式", "字体", "页码")),
    ("法人", ("法人", "身份证")),
    ("付款", ("付款", "支付比例", "预付款")),
    ("否决", ("否决", "废标", "驳回")),
)

_STATUS_NOTES = {"won", "lost", "submitted", "已中标", "已失败", "已提交"}
_VOLUMES = {"business", "technical"}

logger = logging.getLogger(__name__)


def _hit_tags(text: str) -> list[str]:
    out: list[str] = []
    blob = text or ""
    for label, keys in _RULES:
        if any(k in blob for k in keys):
            out.append(label)
    return out


def _kept_comments(comments: list[str]) -> list[str]:
    out: list[str] = []
    for raw in comments:
        text = str(raw or "").strip()
        if not text or text in _STATUS_NOTES:
            continue
        out.append(text)
    return out


def plan_marks(
    *,
    status: str,
    chapters: list[dict],
    warnings: list[str] | None = None,
    comments: list[str] | None = None,
    existing: dict | None = None,
) -> list[dict]:
    """返回要写入的行。source=manual 的章节不在结果里。"""
    st = (status or "").strip()
    if st not in {"won", "lost"}:
        return []
    held = existing or {}
    warn_text = "\n".join(str(x) for x in (warnings or []) if str(x).strip())
    kept = _kept_comments(list(comments or []))
    warn_tags = _hit_tags(warn_text)
    cmt_tags = _hit_tags("\n".join(kept))
    tags: list[str] = []
    note = ""
    source = "auto"
    if st == "lost":
        seen = set(warn_tags) | set(cmt_tags)
        tags = [label for label, _keys in _RULES if label in seen]
        if kept and not cmt_tags:
            tags.append("其他")
        note = "；".join(kept)[:500]
        source = "auto" if warn_tags or not kept else "approval"
    rows: list[dict] = []
    for ch in chapters:
        vol = str(ch.get("volume") or "")
        title = str(ch.get("title") or "").strip()[:40]
        if vol not in _VOLUMES or not title:
            continue
        old = held.get((vol, title)) or {}
        if old.get("source") == "manual":
            continue
        if old.get("source") == "approval" and old.get("verdict") == "good":
            continue
        rows.append(
            {
                "volume": vol,
                "title": title,
                "verdict": "good" if st == "won" else "bad",
                "problemTags": [] if st == "won" else tags,
                "note": "" if st == "won" else note,
                "source": "auto" if st == "won" else source,
            }
        )
    return rows


def _doc_id(value: str | None) -> str | None:
    text = (value or "").strip()
    return text or None


def _chapter_title(tags: list) -> str:
    for tag in tags or []:
        text = str(tag).strip()
        if not text or text == "tech_bid":
            continue
        if text.startswith(("record:", "volume:", "outcome:")):
            continue
        return text[:40]
    return ""


def _tag_value(tags: list, prefix: str) -> str:
    for tag in tags or []:
        text = str(tag)
        if text.startswith(prefix):
            return text[len(prefix) :]
    return ""


def _view(mark: TenderBidMark | None, *, volume: str, title: str, doc_id: str = "") -> dict:
    tags = list(mark.problem_tags or []) if mark is not None else []
    return {
        "volume": volume,
        "chapterTitle": title,
        "docId": (mark.doc_public_id if mark is not None and mark.doc_public_id else doc_id) or "",
        "verdict": (mark.verdict if mark is not None else "none") or "none",
        "problemTags": [t for t in PROBLEM_TAGS if t in set(tags)],
        "note": (mark.note if mark is not None else "") or "",
        "source": (mark.source if mark is not None else "") or "",
    }


async def _marks_of(db: AsyncSession, record_id: int) -> list[TenderBidMark]:
    rows = (
        await db.execute(select(TenderBidMark).where(TenderBidMark.record_id == record_id))
    ).scalars().all()
    return list(rows)


async def _reject_comments(db: AsyncSession, record_id: int) -> list[str]:
    rows = (
        await db.execute(
            select(TenderApprovalLog.comment).where(
                TenderApprovalLog.record_id == record_id,
                TenderApprovalLog.action == "reject",
            )
        )
    ).all()
    return [str(r[0] or "") for r in rows]


def _clean_note(note: str, brief) -> str:
    text = (note or "").strip()
    if not text or brief is None:
        return text[:500]
    from api.services.tenders.bid_archive import sanitize_text

    return sanitize_text(text, brief).strip()[:500]


async def _kb_chapters(db: AsyncSession, record_id: int) -> list[dict]:
    from api.services.tenders.bid_archive import HIST_BASE_ID, TECH_BID_TAG
    from db.models.knowledge import KnowledgeBase, KnowledgeDocument

    base = (
        await db.execute(select(KnowledgeBase).where(KnowledgeBase.public_id == HIST_BASE_ID))
    ).scalar_one_or_none()
    if base is None:
        return []
    docs = (
        await db.execute(select(KnowledgeDocument).where(KnowledgeDocument.base_id == base.id))
    ).scalars().all()
    needle = f"record:{record_id}"
    out: list[dict] = []
    for doc in docs:
        tags = list(doc.tags or [])
        labels = [str(t) for t in tags]
        if needle not in labels or TECH_BID_TAG not in labels:
            continue
        title = _chapter_title(labels)
        vol = _tag_value(labels, "volume:")
        if not title or vol not in _VOLUMES:
            continue
        out.append({"volume": vol, "title": title, "docId": doc.public_id or ""})
    return out


def _file_chapters(row: TenderRecord) -> list[dict]:
    from api.services.tenders.assets import tenders_output_dir
    from api.services.tenders.bid_archive import _brief_of, _files_of, prepare_chapters, resolve_chapters
    from api.services.tenders.outline import items_for_volume

    brief = _brief_of(row)
    root = tenders_output_dir()
    out: list[dict] = []
    for vol, filename in _files_of(row, None):
        path = root / filename
        if not path.is_file():
            continue
        try:
            items = items_for_volume(brief, vol)
        except Exception:
            logger.exception("章节初标读大纲失败 record=%s", row.id)
            items = []
        try:
            ready = prepare_chapters(
                resolve_chapters(items, path),
                record_id=int(row.id),
                volume=vol,
                brief=brief,
            )
        except Exception:
            logger.exception("章节初标读标书失败 record=%s", row.id)
            continue
        for item in ready:
            out.append({"volume": item["volume"], "title": item["title"], "docId": ""})
    return out


async def chapters_for_record(db: AsyncSession, row: TenderRecord) -> list[dict]:
    by: dict[tuple[str, str], dict] = {}
    for ch in _file_chapters(row):
        by[(ch["volume"], ch["title"])] = ch
    for ch in await _kb_chapters(db, int(row.id)):
        by[(ch["volume"], ch["title"])] = ch
    return list(by.values())


def _existing_map(rows: list[TenderBidMark]) -> dict:
    return {
        (m.volume, m.chapter_title): {"source": m.source, "verdict": m.verdict}
        for m in rows
    }


async def _upsert_plan(
    db: AsyncSession,
    *,
    record_id: int | None,
    chapters: list[dict],
    planned: list[dict],
    brief,
    user_id: int | None,
) -> None:
    doc_of = {(c["volume"], c["title"]): _doc_id(c.get("docId")) for c in chapters}
    now = datetime.now(UTC)
    for item in planned:
        mark = await _find_mark(
            db,
            record_id=record_id,
            volume=item["volume"],
            title=item["title"],
            doc_id=doc_of.get((item["volume"], item["title"])) or "",
        )
        doc_id = doc_of.get((item["volume"], item["title"]))
        note = _clean_note(item.get("note") or "", brief)
        if mark is None:
            mark = TenderBidMark(
                public_id=uuid4().hex[:16],
                record_id=record_id,
                doc_public_id=doc_id,
                volume=item["volume"],
                chapter_title=item["title"],
                verdict=item["verdict"],
                problem_tags=list(item.get("problemTags") or []),
                note=note,
                source=item["source"],
                created_by=user_id,
            )
            db.add(mark)
            continue
        if mark.source == "manual":
            if doc_id:
                mark.doc_public_id = doc_id
                mark.updated_at = now
            continue
        mark.verdict = item["verdict"]
        mark.problem_tags = list(item.get("problemTags") or [])
        mark.note = note
        mark.source = item["source"]
        if doc_id:
            mark.doc_public_id = doc_id
        if record_id and not mark.record_id:
            mark.record_id = record_id
        mark.updated_at = now


async def _find_mark(
    db: AsyncSession,
    *,
    record_id: int | None,
    volume: str,
    title: str,
    doc_id: str = "",
) -> TenderBidMark | None:
    if record_id:
        row = (
            await db.execute(
                select(TenderBidMark).where(
                    TenderBidMark.record_id == record_id,
                    TenderBidMark.volume == volume,
                    TenderBidMark.chapter_title == title,
                )
            )
        ).scalar_one_or_none()
        if row is not None:
            return row
    text = (doc_id or "").strip()
    if not text:
        return None
    return (
        await db.execute(select(TenderBidMark).where(TenderBidMark.doc_public_id == text))
    ).scalar_one_or_none()


async def _apply_status(
    db: AsyncSession,
    row: TenderRecord | None,
    chapters: list[dict],
    status: str,
    *,
    record_id: int | None = None,
) -> None:
    rid = int(row.id) if row is not None else record_id
    held_rows = await _marks_of(db, rid) if rid else []
    comments = await _reject_comments(db, rid) if rid else []
    warnings = []
    brief = None
    user_id = None
    if row is not None:
        warnings = [str(x) for x in (row.warnings or []) if str(x).strip()]
        user_id = int(row.user_id) if row.user_id else None
        from api.services.tenders.bid_archive import _brief_of

        brief = _brief_of(row)
    planned = plan_marks(
        status=status,
        chapters=chapters,
        warnings=warnings,
        comments=comments,
        existing=_existing_map(held_rows),
    )
    if not planned and not chapters:
        return
    await _upsert_plan(
        db,
        record_id=rid,
        chapters=chapters,
        planned=planned,
        brief=brief,
        user_id=user_id,
    )


async def _point_docs(db: AsyncSession, record_id: int | None, saved: list[dict]) -> None:
    now = datetime.now(UTC)
    for item in saved:
        doc_id = _doc_id(item.get("docId"))
        if not doc_id:
            continue
        mark = await _find_mark(
            db,
            record_id=record_id,
            volume=str(item.get("volume") or ""),
            title=str(item.get("title") or ""),
            doc_id=doc_id,
        )
        if mark is None:
            continue
        mark.doc_public_id = doc_id
        if record_id and not mark.record_id:
            mark.record_id = record_id
        mark.updated_at = now


async def seed_record_marks(db: AsyncSession, row: TenderRecord) -> None:
    status = (row.status or "").strip()
    if status not in {"won", "lost"}:
        return
    chapters = await chapters_for_record(db, row)
    if not chapters:
        return
    try:
        await _apply_status(db, row, chapters, status)
        await db.commit()
    except Exception:
        await db.rollback()
        raise


async def bind_archived(db: AsyncSession, record_id: int, saved: list[dict]) -> None:
    items = [s for s in saved if s.get("title") and s.get("volume") in _VOLUMES]
    if not items:
        return
    row = await db.get(TenderRecord, int(record_id)) if record_id else None
    rid = int(row.id) if row is not None else (int(record_id) if record_id else None)
    if row is None:
        rid = None
    try:
        await _point_docs(db, rid, items)
        status = ""
        if row is not None and (row.status or "") in {"won", "lost"}:
            status = row.status or ""
        else:
            outcomes = {str(s.get("outcome") or "") for s in items}
            outcomes.discard("")
            if outcomes == {"won"}:
                status = "won"
            elif "lost" in outcomes and "won" not in outcomes:
                status = "lost"
        if status:
            chapters = [
                {
                    "volume": s["volume"],
                    "title": s["title"],
                    "docId": s.get("docId") or "",
                }
                for s in items
            ]
            await _apply_status(db, row, chapters, status, record_id=rid)
        await db.commit()
    except Exception:
        await db.rollback()
        raise


async def list_history_chapters(
    db: AsyncSession,
    record_public_id: str,
    *,
    user,
    admin: bool,
) -> dict:
    from api.services.tenders.records import _assert_owner_or_admin, _get_row

    row = await _get_row(db, record_public_id)
    _assert_owner_or_admin(row, user, admin=admin)
    chapters = await chapters_for_record(db, row)
    marks = await _marks_of(db, int(row.id))
    by = {(c["volume"], c["title"]): c for c in chapters}
    mark_by = {(m.volume, m.chapter_title): m for m in marks}
    for m in marks:
        key = (m.volume, m.chapter_title)
        if key not in by:
            by[key] = {
                "volume": m.volume,
                "title": m.chapter_title,
                "docId": m.doc_public_id or "",
            }
    items = []
    for key in sorted(by, key=lambda k: (0 if k[0] == "business" else 1, k[1])):
        ch = by[key]
        items.append(
            _view(
                mark_by.get(key),
                volume=ch["volume"],
                title=ch["title"],
                doc_id=ch.get("docId") or "",
            )
        )
    return {"recordId": row.public_id, "chapters": items}


async def save_manual_mark(db: AsyncSession, body, *, user, admin: bool) -> dict:
    from api.services.tenders.records import _assert_owner_or_admin, _get_row

    verdict = (body.verdict or "").strip()
    if verdict not in {"good", "bad", "none"}:
        raise AppError(ErrorCode.VALIDATION, "标记只能是可参考、需规避或未标记", status_code=422)
    volume = (body.volume or "").strip()
    if volume not in _VOLUMES:
        raise AppError(ErrorCode.VALIDATION, "分册只能是商务标或技术标", status_code=422)
    title = (body.chapterTitle or "").strip()[:40]
    if not title:
        raise AppError(ErrorCode.VALIDATION, "章节标题不能为空", status_code=422)
    picked = {str(t).strip() for t in (body.problemTags or [])}
    tags = [t for t in PROBLEM_TAGS if t in picked] if verdict == "bad" else []
    note = (body.note or "").strip()[:500]
    record_id = (body.recordId or "").strip()
    doc_id = (body.docId or "").strip()
    row = None
    if record_id:
        row = await _get_row(db, record_id)
        _assert_owner_or_admin(row, user, admin=admin)
        from api.services.tenders.bid_archive import _brief_of

        note = _clean_note(note, _brief_of(row))
    elif doc_id:
        await _assert_doc(db, doc_id, user=user, admin=admin)
    else:
        raise AppError(ErrorCode.VALIDATION, "缺少投标记录或章节", status_code=422)
    rid = int(row.id) if row is not None else None
    mark = await _find_mark(db, record_id=rid, volume=volume, title=title, doc_id=doc_id)
    now = datetime.now(UTC)
    if mark is None:
        mark = TenderBidMark(
            public_id=uuid4().hex[:16],
            record_id=rid,
            doc_public_id=_doc_id(doc_id),
            volume=volume,
            chapter_title=title,
            verdict=verdict,
            problem_tags=tags,
            note=note,
            source="manual",
            created_by=int(user.id),
        )
        db.add(mark)
    else:
        if not admin and mark.record_id and row is None:
            owner = await db.get(TenderRecord, int(mark.record_id))
            if owner is not None:
                _assert_owner_or_admin(owner, user, admin=admin)
        mark.verdict = verdict
        mark.problem_tags = tags
        mark.note = note
        mark.source = "manual"
        if rid and not mark.record_id:
            mark.record_id = rid
        if doc_id and not mark.doc_public_id:
            mark.doc_public_id = doc_id
        mark.updated_at = now
    await db.commit()
    await db.refresh(mark)
    return _view(mark, volume=mark.volume, title=mark.chapter_title)


async def _assert_doc(db: AsyncSession, doc_id: str, *, user, admin: bool) -> None:
    from db.models.knowledge import KnowledgeDocument

    doc = (
        await db.execute(select(KnowledgeDocument).where(KnowledgeDocument.public_id == doc_id))
    ).scalar_one_or_none()
    if doc is None:
        raise AppError(ErrorCode.NOT_FOUND, "章节不存在", status_code=404)
    if admin or (doc.created_by and int(doc.created_by) == int(user.id)):
        return
    raise AppError(ErrorCode.FORBIDDEN, "只能操作本人的投标任务", status_code=403)
