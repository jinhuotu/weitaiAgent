"""知识库文件入库：进程内串行 worker + 持久化任务表。"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from common.config import get_settings
from common.times import to_epoch_ms
from db.models.knowledge import KnowledgeBase, KnowledgeDocument, KnowledgeIngestTask
from db.session import AsyncSessionLocal

logger = logging.getLogger("api.kb.queue")

_wakeup: asyncio.Event | None = None
_worker_task: asyncio.Task | None = None
_watchdog_task: asyncio.Task | None = None


def _parse_timeout_seconds() -> int:
    return max(60, int(get_settings().kb_parse_timeout_seconds))


def _timeout_cutoff() -> datetime:
    return datetime.now(timezone.utc) - timedelta(seconds=_parse_timeout_seconds())


async def _fail_ingest_task_and_doc(
    db: AsyncSession,
    task: KnowledgeIngestTask,
    *,
    message: str,
) -> None:
    msg = (message or "解析失败")[:500]
    task.status = "failed"
    task.progress = 100
    task.error_msg = msg
    task.finished_at = datetime.now(timezone.utc)
    doc = await db.get(KnowledgeDocument, task.document_id)
    if doc is not None and doc.status == "parsing":
        doc.status = "failed"
        doc.error_msg = msg


def _event() -> asyncio.Event:
    global _wakeup
    if _wakeup is None:
        _wakeup = asyncio.Event()
    return _wakeup


def notify_ingest_worker() -> None:
    try:
        _event().set()
    except RuntimeError:
        pass


def short_task_id(n: int = 12) -> str:
    import secrets

    return secrets.token_hex(n)[:n]


def to_task_item(row: KnowledgeIngestTask, *, doc_name: str | None = None) -> dict[str, Any]:
    return {
        "id": row.public_id,
        "baseId": getattr(row, "_base_public_id", None),
        "docId": getattr(row, "_doc_public_id", None),
        "docName": doc_name or getattr(row, "_doc_name", None),
        "status": row.status,
        "progress": int(row.progress or 0),
        "errorMsg": row.error_msg,
        "createdAt": to_epoch_ms(row.created_at),
        "startedAt": to_epoch_ms(row.started_at) if row.started_at else 0,
        "finishedAt": to_epoch_ms(row.finished_at) if row.finished_at else 0,
    }


async def enqueue_ingest_task(
    db: AsyncSession,
    *,
    base: KnowledgeBase,
    doc: KnowledgeDocument,
    force_reextract: bool = False,
) -> KnowledgeIngestTask:
    row = KnowledgeIngestTask(
        public_id=short_task_id(12),
        base_id=base.id,
        document_id=doc.id,
        status="queued",
        progress=0,
        force_reextract=bool(force_reextract),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    notify_ingest_worker()
    return row


async def list_ingest_tasks(
    db: AsyncSession, *, base_public_id: str, limit: int = 40
) -> list[dict[str, Any]]:
    from api.services.knowledge.bases import get_base_by_public_id

    base = await get_base_by_public_id(db, base_public_id)
    result = await db.execute(
        select(KnowledgeIngestTask)
        .where(KnowledgeIngestTask.base_id == base.id)
        .where(KnowledgeIngestTask.status != "cancelled")
        .order_by(KnowledgeIngestTask.created_at.desc())
        .limit(max(1, min(limit, 100)))
    )
    rows = list(result.scalars().all())
    doc_ids = {r.document_id for r in rows}
    names: dict[int, str] = {}
    pids: dict[int, str] = {}
    if doc_ids:
        docs = await db.execute(
            select(KnowledgeDocument.id, KnowledgeDocument.public_id, KnowledgeDocument.name).where(
                KnowledgeDocument.id.in_(doc_ids)
            )
        )
        for did, pid, name in docs.all():
            names[int(did)] = str(name)
            pids[int(did)] = str(pid)
    items: list[dict[str, Any]] = []
    for row in rows:
        setattr(row, "_base_public_id", base.public_id)
        setattr(row, "_doc_public_id", pids.get(row.document_id))
        setattr(row, "_doc_name", names.get(row.document_id))
        items.append(to_task_item(row, doc_name=names.get(row.document_id)))
    return items


async def cancel_ingest_task(
    db: AsyncSession, *, base_public_id: str, task_public_id: str
) -> dict[str, Any]:
    from api.services.knowledge.bases import get_base_by_public_id
    from common.errors import AppError, ErrorCode

    base = await get_base_by_public_id(db, base_public_id)
    result = await db.execute(
        select(KnowledgeIngestTask).where(
            KnowledgeIngestTask.public_id == task_public_id,
            KnowledgeIngestTask.base_id == base.id,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise AppError(ErrorCode.NOT_FOUND, "ingest task not found", status_code=404)
    if row.status not in {"queued"}:
        raise AppError(ErrorCode.CONFLICT, "只能取消排队中的任务", status_code=409)
    row.status = "cancelled"
    row.finished_at = datetime.now(timezone.utc)
    doc = await db.get(KnowledgeDocument, row.document_id)
    if doc is not None and doc.status == "parsing":
        doc.status = "failed"
        doc.error_msg = "已取消入库"
    await db.delete(row)
    await db.commit()
    return {"id": task_public_id, "cancelled": True}


async def fail_timed_out_ingest_tasks() -> int:
    """将超过 KB_PARSE_TIMEOUT_SECONDS 仍为 running 的任务与文档标为失败。"""
    cutoff = _timeout_cutoff()
    limit_sec = _parse_timeout_seconds()
    n = 0
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(KnowledgeIngestTask).where(KnowledgeIngestTask.status == "running")
        )
        for task in result.scalars().all():
            started = task.started_at or task.created_at
            if started and started < cutoff:
                await _fail_ingest_task_and_doc(
                    db,
                    task,
                    message=f"解析超时（超过 {limit_sec} 秒），请重试",
                )
                n += 1
        if n:
            await db.commit()
    if n:
        logger.warning("ingest timed-out tasks marked failed count=%s", n)
    return n


async def reclaim_stale_tasks() -> int:
    """启动：超时 running 标失败；其余 running 收回 queued；无任务的 parsing 标失败。"""
    cutoff = _timeout_cutoff()
    limit_sec = _parse_timeout_seconds()
    async with AsyncSessionLocal() as db:
        now = datetime.now(timezone.utc)
        running = list(
            (
                await db.execute(
                    select(KnowledgeIngestTask).where(KnowledgeIngestTask.status == "running")
                )
            )
            .scalars()
            .all()
        )
        n = 0
        for task in running:
            started = task.started_at or task.created_at
            if started and started < cutoff:
                await _fail_ingest_task_and_doc(
                    db,
                    task,
                    message=f"解析超时（超过 {limit_sec} 秒），请重试",
                )
                n += 1
            else:
                task.status = "queued"
                task.progress = 0
                task.started_at = None
                task.updated_at = now
        queued_doc_ids = set(
            (
                await db.execute(
                    select(KnowledgeIngestTask.document_id).where(
                        KnowledgeIngestTask.status.in_(("queued", "running"))
                    )
                )
            )
            .scalars()
            .all()
        )
        parsing = list(
            (
                await db.execute(
                    select(KnowledgeDocument).where(KnowledgeDocument.status == "parsing")
                )
            )
            .scalars()
            .all()
        )
        for doc in parsing:
            if doc.id in queued_doc_ids:
                continue
            doc.status = "failed"
            doc.error_msg = "解析中断（服务重启或处理中断），请重试"
            n += 1
        await db.commit()
        logger.info("ingest reclaim running→queued; stale parsing failed=%s", n)
        return n


async def _claim_next(db: AsyncSession) -> KnowledgeIngestTask | None:
    result = await db.execute(
        select(KnowledgeIngestTask)
        .where(KnowledgeIngestTask.status == "queued")
        .order_by(KnowledgeIngestTask.id.asc())
        .limit(1)
    )
    row = result.scalar_one_or_none()
    if row is None:
        return None
    row.status = "running"
    row.started_at = datetime.now(timezone.utc)
    row.progress = 5
    await db.commit()
    await db.refresh(row)
    return row


async def _run_claimed(task: KnowledgeIngestTask) -> None:
    from api.services.knowledge.ingest import process_uploaded_document
    from api.services.knowledge.video_contract import ERR_ASR_TIMEOUT, is_video_ext

    doc_pid = ""
    force = bool(task.force_reextract)
    is_video = False
    async with AsyncSessionLocal() as db:
        row = await db.get(KnowledgeIngestTask, task.id)
        if row is None or row.status == "cancelled":
            return
        doc = await db.get(KnowledgeDocument, row.document_id)
        if doc is None:
            row.status = "failed"
            row.error_msg = "文档已删除"
            row.finished_at = datetime.now(timezone.utc)
            await db.commit()
            return
        doc_pid = doc.public_id
        is_video = (doc.kind or "") == "video" or is_video_ext(doc.file_type)
        row.progress = 20
        await db.commit()
    try:
        timeout = _parse_timeout_seconds()
        if is_video:
            timeout = max(
                timeout,
                max(60, int(get_settings().kb_video_asr_timeout_seconds)),
            )
        await asyncio.wait_for(
            process_uploaded_document(doc_pid, force_reextract=force),
            timeout=timeout,
        )
        async with AsyncSessionLocal() as db:
            row = await db.get(KnowledgeIngestTask, task.id)
            if row is None or row.status != "running":
                return
            row.status = "succeeded"
            row.progress = 100
            row.finished_at = datetime.now(timezone.utc)
            row.error_msg = None
            await db.commit()
    except TimeoutError:
        msg = (
            ERR_ASR_TIMEOUT
            if is_video
            else f"解析超时（超过 {_parse_timeout_seconds()} 秒），请重试"
        )
        logger.warning(
            "ingest task timeout id=%s doc=%s video=%s",
            task.public_id,
            doc_pid,
            is_video,
        )
        async with AsyncSessionLocal() as db:
            row = await db.get(KnowledgeIngestTask, task.id)
            if row is not None and row.status == "running":
                await _fail_ingest_task_and_doc(db, row, message=msg)
                await db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.exception("ingest task failed id=%s", task.public_id)
        async with AsyncSessionLocal() as db:
            row = await db.get(KnowledgeIngestTask, task.id)
            if row is None:
                return
            row.status = "failed"
            row.progress = 100
            from common.errors import AppError

            row.error_msg = (
                (exc.msg or str(exc))[:500]
                if isinstance(exc, AppError)
                else str(exc)[:500]
            )
            row.finished_at = datetime.now(timezone.utc)
            await db.commit()


async def _ingest_timeout_watchdog() -> None:
    while True:
        try:
            await asyncio.sleep(60)
            await fail_timed_out_ingest_tasks()
        except asyncio.CancelledError:
            logger.info("kb ingest timeout watchdog cancelled")
            raise
        except Exception:  # noqa: BLE001
            logger.exception("kb ingest timeout watchdog error")


async def run_ingest_worker() -> None:
    logger.info("kb ingest worker started")
    ev = _event()
    while True:
        try:
            async with AsyncSessionLocal() as db:
                claimed = await _claim_next(db)
            if claimed is None:
                ev.clear()
                try:
                    await asyncio.wait_for(ev.wait(), timeout=2.0)
                except TimeoutError:
                    continue
                continue
            await _run_claimed(claimed)
        except asyncio.CancelledError:
            logger.info("kb ingest worker cancelled")
            raise
        except Exception:  # noqa: BLE001
            logger.exception("kb ingest worker loop error")
            await asyncio.sleep(1.0)


def start_ingest_worker() -> None:
    global _worker_task, _watchdog_task
    if _worker_task is not None and not _worker_task.done():
        return
    _worker_task = asyncio.create_task(run_ingest_worker(), name="kb-ingest-worker")
    if _watchdog_task is None or _watchdog_task.done():
        _watchdog_task = asyncio.create_task(
            _ingest_timeout_watchdog(),
            name="kb-ingest-timeout-watchdog",
        )
