"""历史标书库：按章节存写法。正文先剥项目名和报价，再交给 ingest_text。"""

from __future__ import annotations

import asyncio
import logging
import re
import tempfile
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.services.tenders.history import BIDDER_LOCK, contains_foreign_bidder
from api.services.tenders.schema import BidBrief
from common.errors import AppError, ErrorCode
from db.models.knowledge import KnowledgeBase, KnowledgeDocument
from db.models.tender import TenderRecord

logger = logging.getLogger(__name__)

HIST_BASE_ID = "tenderhist01"
HIST_BASE_NAME = "历史标书库"
TECH_BID_TAG = "tech_bid"
_MIN_CHARS = 40
_MAX_UPLOAD = 40 * 1024 * 1024

_VOL_NAME = {"business": "商务标", "technical": "技术标"}
_DROP_MARKS = (
    "目录",
    "目次",
    "封面",
    "报价表",
    "报价清单",
    "投标报价",
    "已标价",
    "工程量清单",
    "开标一览",
    "类似业绩",
    "类似项目",
)
_H1_RE = re.compile(
    r"^(?:第[一二三四五六七八九十百零〇两\d]{1,4}章|[一二三四五六七八九十]{1,3}、)\S"
)
_MONEY_RE = re.compile(
    r"(?:人民币|RMB|￥|¥)\s*[0-9０-９][0-9０-９,，.]*"
    r"|[0-9０-９][0-9０-９,，.]*\s*(?:万元|万|元整|元)"
    r"|[零壹贰叁肆伍陆柒捌玖拾佰仟万亿]{2,}[圆元]整?"
)
_ID_RE = re.compile(r"\d{17}[\dXx]")
_PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_COMPANY_RE = re.compile(r"[\u4e00-\u9fffA-Za-z0-9（）()]{2,40}有限公司")

_pending: set[asyncio.Task] = set()


def drop_title(title: str) -> bool:
    n = re.sub(r"\s+", "", title or "")
    n = re.sub(r"^[一二三四五六七八九十]+、", "", n)
    n = re.sub(r"^第[一二三四五六七八九十百零〇两\d]+章", "", n)
    return any(mark in n for mark in _DROP_MARKS)


def sanitize_text(text: str, brief: BidBrief | None = None) -> str:
    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    if not raw.strip():
        return ""
    raw = raw.replace(BIDDER_LOCK, "投标人")
    bidder = ""
    if brief is not None:
        bidder = (brief.bidderName or "").strip()
    if bidder and bidder not in {"投标人", BIDDER_LOCK} and len(bidder) >= 4:
        raw = raw.replace(bidder, "投标人")
    for needle in _needles(brief):
        raw = raw.replace(needle, "")
    raw = _ID_RE.sub("", raw)
    raw = _PHONE_RE.sub("", raw)
    raw = _EMAIL_RE.sub("", raw)
    raw = _MONEY_RE.sub("（金额略）", raw)
    kept: list[str] = []
    for line in raw.split("\n"):
        if _digit_line(line):
            continue
        kept.append(_drop_foreign_sentences(line))
    return "\n".join(part for part in kept if part.strip()).strip()


def chapters_from_outline(items) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    title = ""
    parts: list[str] = []

    def flush() -> None:
        nonlocal title, parts
        if title or parts:
            body = "\n".join(p for p in parts if p.strip())
            out.append((title, body))
        title = ""
        parts = []

    for item in items or []:
        if getattr(item, "skipped", False):
            continue
        level = int(getattr(item, "level", 1) or 1)
        head = (getattr(item, "rawTitle", "") or getattr(item, "title", "") or "").strip()
        body = (getattr(item, "draftBody", "") or "").strip() or (getattr(item, "body", "") or "").strip()
        if level <= 1:
            flush()
            title = head
            if body:
                parts.append(body)
        else:
            if not title:
                title = head
            elif head:
                parts.append(head)
            if body:
                parts.append(body)
    flush()
    return out


def chapters_from_docx(path: Path) -> list[tuple[str, str]]:
    from docx import Document
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    doc = Document(str(path))
    chunks: list[tuple[str, list[str]]] = [("封面", [])]
    for child in doc.element.body.iterchildren():
        if child.tag == qn("w:p"):
            para = Paragraph(child, doc)
            text = (para.text or "").strip()
            if not text:
                continue
            if _is_h1(para):
                chunks.append((text, []))
                continue
            chunks[-1][1].append(text)
        elif child.tag == qn("w:tbl"):
            table = Table(child, doc)
            lines: list[str] = []
            for row in table.rows:
                cells = []
                seen: set[str] = set()
                for cell in row.cells:
                    t = (cell.text or "").strip()
                    if t and t not in seen:
                        seen.add(t)
                        cells.append(t)
                if cells:
                    lines.append(" ".join(cells))
            if lines:
                chunks[-1][1].append("\n".join(lines))
    out: list[tuple[str, str]] = []
    for title, lines in chunks:
        body = "\n".join(lines).strip()
        if title or body:
            out.append((title, body))
    return out


def resolve_chapters(items, docx_path: Path | None) -> list[tuple[str, str]]:
    outlined = chapters_from_outline(items)
    docx_ch = chapters_from_docx(docx_path) if docx_path is not None and docx_path.is_file() else []
    if not outlined:
        return docx_ch
    return _fill_from_docx(outlined, docx_ch)


def prepare_chapters(
    chapters: list[tuple[str, str]],
    *,
    record_id: int,
    volume: str,
    brief: BidBrief | None = None,
    outcome: str = "",
) -> list[dict]:
    vol = volume if volume in _VOL_NAME else "business"
    label = _VOL_NAME[vol]
    ready: list[dict] = []
    for title, body in chapters:
        if drop_title(title):
            continue
        clean_title = sanitize_text(title, brief).strip() or "章节"
        clean_body = sanitize_text(body, brief)
        if len(re.sub(r"\s+", "", clean_body)) < _MIN_CHARS:
            continue
        tags = [TECH_BID_TAG, clean_title[:40], f"volume:{vol}"]
        if record_id:
            tags.append(f"record:{int(record_id)}")
        if outcome in {"won", "lost"}:
            tags.append(f"outcome:{outcome}")
        ready.append(
            {
                "title": clean_title[:40],
                "body": clean_body,
                "name": f"{label}·{clean_title}"[:255],
                "tags": tags,
                "volume": vol,
            }
        )
    return ready


def schedule_archive(record_id: int, volumes: list[str] | None = None) -> None:
    _spawn(_archive_job(int(record_id), volumes))


def schedule_upload(
    *,
    user_id: int,
    username: str,
    chapters: list[dict],
    record_id: int = 0,
) -> None:
    _spawn(_upload_job(int(user_id), username, chapters, int(record_id or 0)))


async def forget_record(db: AsyncSession, record_id: int, volumes: list[str] | None = None) -> None:
    try:
        base = await _find_base(db)
        if base is None:
            return
        await _delete_tagged(db, base, record_id=int(record_id), volumes=volumes)
    except Exception:
        logger.exception("历史标书章节删除失败 record=%s", record_id)


async def accept_history_upload(
    db: AsyncSession,
    *,
    data: bytes,
    filename: str,
    user_id: int,
    username: str,
    outcome: str,
    record_public_id: str,
    admin: bool,
) -> dict[str, object]:
    mark = (outcome or "").strip().lower()
    if mark not in {"", "won", "lost"}:
        raise AppError(ErrorCode.VALIDATION, "结果状态只能是 won 或 lost", status_code=422)
    name = Path((filename or "upload.docx").replace("\\", "/").split("/")[-1]).name
    if Path(name).suffix.lower() != ".docx":
        raise AppError(ErrorCode.VALIDATION, "仅支持 docx", status_code=422)
    if not data:
        raise AppError(ErrorCode.VALIDATION, "上传文件为空", status_code=422)
    if len(data) > _MAX_UPLOAD:
        raise AppError(ErrorCode.VALIDATION, "单个文件不能超过 40MB", status_code=422)

    brief = BidBrief()
    record_pk = 0
    pid = (record_public_id or "").strip()
    if pid:
        row = (
            await db.execute(select(TenderRecord).where(TenderRecord.public_id == pid))
        ).scalar_one_or_none()
        if row is None:
            raise AppError(ErrorCode.NOT_FOUND, "投标记录不存在", status_code=404)
        if not admin and int(row.user_id) != int(user_id):
            raise AppError(ErrorCode.FORBIDDEN, "只能关联本人的投标任务", status_code=403)
        record_pk = int(row.id)
        brief = _brief_of(row)

    from api.services.tenders.categories import slot_volume

    tmp = tempfile.NamedTemporaryFile(suffix=".docx", delete=False)
    tmp.write(data)
    tmp.close()
    path = Path(tmp.name)
    try:
        raw = chapters_from_docx(path)
    finally:
        path.unlink(missing_ok=True)

    grouped: dict[str, list[tuple[str, str]]] = {}
    for title, body in raw:
        vol = slot_volume("", title)
        grouped.setdefault(vol, []).append((title, body))
    ready: list[dict] = []
    for vol, chapters in grouped.items():
        ready.extend(
            prepare_chapters(chapters, record_id=record_pk, volume=vol, brief=brief, outcome=mark)
        )
    if not ready:
        raise AppError(ErrorCode.VALIDATION, "没有可入库的章节", status_code=422)
    schedule_upload(user_id=user_id, username=username, chapters=ready, record_id=record_pk)
    return {"queued": len(ready), "recordId": pid}


def _spawn(coro) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        coro.close()
        logger.warning("历史标书入库未挂上，没有事件循环")
        return
    task = loop.create_task(coro)
    _pending.add(task)
    task.add_done_callback(_pending.discard)


async def _archive_job(record_id: int, volumes: list[str] | None) -> None:
    from db.session import AsyncSessionLocal

    try:
        async with AsyncSessionLocal() as db:
            row = await db.get(TenderRecord, record_id)
            if row is None:
                return
            brief = _brief_of(row)
            from api.services.tenders.assets import tenders_output_dir
            from api.services.tenders.outline import items_for_volume

            root = tenders_output_dir()
            for vol, filename in _files_of(row, volumes):
                path = root / filename
                if not path.is_file():
                    continue
                items = []
                try:
                    items = items_for_volume(brief, vol)
                except Exception:
                    logger.exception("历史标书大纲读取失败 record=%s", record_id)
                    items = []
                chapters = resolve_chapters(items, path)
                ready = prepare_chapters(chapters, record_id=record_id, volume=vol, brief=brief)

                async def current(vol_name=vol, expect=filename) -> bool:
                    fresh = await db.get(TenderRecord, record_id)
                    if fresh is None:
                        return False
                    return _file_name(fresh, vol_name) == expect

                if not await current():
                    continue
                await _delete_tagged(
                    db,
                    await ensure_history_base(db, created_by=row.user_id),
                    record_id=record_id,
                    volumes=[vol],
                )
                if not await current():
                    await forget_record(db, record_id, volumes=[vol])
                    continue
                if not ready:
                    continue
                saved = await _index(
                    db,
                    ready,
                    user_id=int(row.user_id or 0),
                    username=row.username or "",
                    guard=current,
                )
                await _bind_marks(db, record_id, saved)
            fresh = await db.get(TenderRecord, record_id)
            if fresh is None:
                await forget_record(db, record_id)
    except Exception:
        logger.exception("历史标书入库失败 record=%s", record_id)
        await _note_failure(record_id)


async def _upload_job(user_id: int, username: str, chapters: list[dict], record_id: int) -> None:
    from db.session import AsyncSessionLocal

    try:
        async with AsyncSessionLocal() as db:
            if record_id:
                row = await db.get(TenderRecord, record_id)
                if row is None:
                    return

            async def current() -> bool:
                if not record_id:
                    return True
                return await db.get(TenderRecord, record_id) is not None

            saved = await _index(db, chapters, user_id=user_id, username=username, guard=current)
            await _bind_marks(db, record_id, saved)
    except Exception:
        logger.exception("历史上传标书入库失败 record=%s", record_id or 0)
        if record_id:
            await _note_failure(record_id)


def _outcome_of(tags) -> str:
    for tag in tags or []:
        text = str(tag)
        if text.startswith("outcome:"):
            return text[len("outcome:") :]
    return ""


async def _bind_marks(db: AsyncSession, record_id: int, saved: list[dict]) -> None:
    if not saved:
        return
    try:
        from api.services.tenders.bid_marks import bind_archived

        await bind_archived(db, record_id, saved)
    except Exception:
        logger.exception("章节初标回填失败 record=%s", record_id or 0)


async def _index(db: AsyncSession, chapters: list[dict], *, user_id: int, username: str, guard) -> list[dict]:
    from api.services.knowledge.ingest import ingest_text, short_id

    saved: list[dict] = []
    base = await ensure_history_base(db, created_by=user_id or None)
    by_vol: dict[str, list[dict]] = {}
    for item in chapters:
        by_vol.setdefault(item.get("volume") or "business", []).append(item)
    for vol, items in by_vol.items():
        if not await guard():
            return saved
        parent = KnowledgeDocument(
            public_id=short_id(12),
            base_id=base.id,
            name=_VOL_NAME.get(vol, "历史标书"),
            source="text",
            kind="doc",
            summary="章节目录",
            tags=_parent_tags(items),
            status="ready",
            review_status="approved",
            created_by=user_id or None,
            uploader=(username or "")[:128] or None,
        )
        db.add(parent)
        await db.flush()
        for item in items:
            if not await guard():
                await db.commit()
                return saved
            out = await ingest_text(
                db,
                base_public_id=base.public_id,
                name=item["name"],
                content=item["body"],
                tags=list(item["tags"]),
                parent_id=parent.public_id,
                skip_dedup=True,
                created_by=user_id or None,
                uploader=(username or "")[:128] or None,
            )
            title = str(item.get("title") or "").strip()[:40]
            doc_id = str(out.get("id") or "").strip() if isinstance(out, dict) else ""
            if title:
                saved.append(
                    {
                        "volume": vol,
                        "title": title,
                        "docId": doc_id,
                        "outcome": _outcome_of(item.get("tags")),
                    }
                )
    return saved


async def ensure_history_base(db: AsyncSession, created_by: int | None = None) -> KnowledgeBase:
    base = await _find_base(db)
    if base is not None:
        return base
    from api.services.knowledge.bases import PURPOSE_RAG, create_base, get_base_by_public_id

    await create_base(
        db,
        name=HIST_BASE_NAME,
        description="本公司历史标书写法，已去掉项目名和报价。",
        purpose=PURPOSE_RAG,
        created_by=created_by,
        public_id=HIST_BASE_ID,
    )
    return await get_base_by_public_id(db, HIST_BASE_ID)


async def _find_base(db: AsyncSession) -> KnowledgeBase | None:
    return (
        await db.execute(select(KnowledgeBase).where(KnowledgeBase.public_id == HIST_BASE_ID))
    ).scalar_one_or_none()


async def _delete_tagged(
    db: AsyncSession,
    base: KnowledgeBase,
    *,
    record_id: int,
    volumes: list[str] | None,
) -> None:
    from api.services.knowledge.ingest import delete_document_record

    docs = (
        await db.execute(select(KnowledgeDocument).where(KnowledgeDocument.base_id == base.id))
    ).scalars().all()
    matched = [doc for doc in docs if _tagged(doc, record_id, volumes)]
    ids = {doc.public_id for doc in matched}
    roots = [doc for doc in matched if not doc.parent_id or doc.parent_id not in ids]
    for doc in roots:
        await delete_document_record(db, base_public_id=base.public_id, doc_public_id=doc.public_id)
    if roots:
        await db.commit()


async def _note_failure(record_id: int) -> None:
    from db.session import AsyncSessionLocal

    try:
        async with AsyncSessionLocal() as db:
            row = await db.get(TenderRecord, record_id)
            if row is None:
                return
            notes = list(row.warnings or [])
            msg = "历史标书写法未入库"
            if msg not in notes:
                notes.append(msg)
                row.warnings = notes
                await db.commit()
    except Exception:
        logger.exception("历史标书失败提示没写上 record=%s", record_id)


def _files_of(row: TenderRecord, volumes: list[str] | None) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    if volumes is None or "business" in volumes:
        name = (row.docx_file or "").strip()
        if name:
            pairs.append(("business", name))
    if volumes is None or "technical" in volumes:
        name = (getattr(row, "tech_docx_file", None) or "").strip()
        if name:
            pairs.append(("technical", name))
    return pairs


def _file_name(row: TenderRecord, volume: str) -> str:
    if volume == "technical":
        return (getattr(row, "tech_docx_file", None) or "").strip()
    return (row.docx_file or "").strip()


def _brief_of(row: TenderRecord) -> BidBrief:
    raw = row.brief_json if isinstance(row.brief_json, dict) else {}
    try:
        return BidBrief.model_validate(raw)
    except Exception:
        return BidBrief()


def _parent_tags(items: list[dict]) -> list[str]:
    tags = [TECH_BID_TAG]
    for item in items:
        for tag in item.get("tags") or []:
            text = str(tag)
            if text.startswith("record:") or text.startswith("volume:"):
                if text not in tags:
                    tags.append(text)
    return tags


def _tagged(doc: KnowledgeDocument, record_id: int, volumes: list[str] | None) -> bool:
    tags = {str(t).strip() for t in (doc.tags or [])}
    if f"record:{int(record_id)}" not in tags:
        return False
    if not volumes:
        return True
    return any(f"volume:{vol}" in tags for vol in volumes)


def _needles(brief: BidBrief | None) -> list[str]:
    if brief is None:
        return []
    vals = [
        brief.projectName,
        brief.tenderer,
        brief.tenderNo,
        brief.bidderAddress,
        brief.bidderPhone,
        brief.bidderFax,
        brief.bidderEmail,
        brief.bidderWebsite,
        brief.legalPersonIdNo,
        brief.agentIdNo,
    ]
    if brief.bidPriceYuan:
        amount = float(brief.bidPriceYuan)
        text = str(int(amount)) if amount == int(amount) else str(amount)
        vals.append(text)
    out: list[str] = []
    for raw in vals:
        s = str(raw or "").strip()
        # 太短的项目名会误伤普通词
        if len(s) < 4 or s in out:
            continue
        out.append(s)
    out.sort(key=len, reverse=True)
    return out


def _digit_line(line: str) -> bool:
    raw = re.sub(r"\s+", "", line or "")
    if len(raw) < 6:
        return False
    digits = sum(ch.isdigit() for ch in raw)
    return digits / len(raw) >= 0.75


def _drop_foreign_sentences(line: str) -> str:
    parts = re.split(r"(?<=[。！？；])", line)
    kept: list[str] = []
    for part in parts:
        if contains_foreign_bidder(part) or _other_company(part):
            continue
        kept.append(part)
    return "".join(kept).strip()


def _other_company(text: str) -> bool:
    for match in _COMPANY_RE.finditer(text or ""):
        name = match.group(0)
        if "投标人" in name or name in BIDDER_LOCK:
            continue
        return True
    return False


def _is_h1(para) -> bool:
    from docx.oxml.ns import qn

    style = ""
    try:
        style = para.style.name or ""
    except Exception:
        style = ""
    low = style.replace(" ", "").lower()
    if "toc" in low or "目录" in style:
        return False
    if low in {"heading1", "标题1"}:
        return True
    ppr = para._p.find(qn("w:pPr"))
    if ppr is not None:
        lvl = ppr.find(qn("w:outlineLvl"))
        raw = "" if lvl is None else (lvl.get(qn("w:val")) or "")
        if raw.isdigit() and int(raw) == 0:
            return True
    text = (para.text or "").strip()
    return bool(_H1_RE.match(text)) and len(text) <= 40


def _fill_from_docx(outlined: list[tuple[str, str]], docx_ch: list[tuple[str, str]]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for title, body in outlined:
        if len(re.sub(r"\s+", "", body)) >= _MIN_CHARS:
            out.append((title, body))
            continue
        got = _find_docx(title, docx_ch)
        out.append((title, got or body))
    return out


def _find_docx(title: str, chapters: list[tuple[str, str]]) -> str:
    key = _compact(title)
    if len(key) < 2:
        return ""
    for other, body in chapters:
        name = _compact(other)
        if not name:
            continue
        if key in name or name in key:
            return body
    return ""


def _compact(title: str) -> str:
    n = re.sub(r"\s+", "", title or "")
    n = re.sub(r"^[一二三四五六七八九十]+、", "", n)
    n = re.sub(r"^第[一二三四五六七八九十百零〇两\d]+章", "", n)
    n = re.sub(r"^[（(][一二三四五六七八九十\d]+[)）]", "", n)
    return n
