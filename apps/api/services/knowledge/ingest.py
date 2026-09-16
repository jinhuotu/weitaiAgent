"""知识库入库与检索：切块 → Embedding → Qdrant；检索带关键词重排。"""

from __future__ import annotations

import hashlib
import logging
import secrets
from pathlib import Path
from typing import Any

from fastapi import UploadFile
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.services.knowledge.bases import get_base_by_public_id
from api.services.knowledge.chunking import is_layout_case_card, split_text
from api.services.knowledge.classify import (
    TAG_BID_FOREIGN,
    apply_skip_rag_tag,
    classify_kb_document,
    has_skip_rag_tag,
    should_skip_kb_retrieval,
)
from api.services.knowledge.drawings import is_drawing_ext, preview_jpeg_from_file
from api.services.knowledge.embeddings import get_embedding_client
from api.services.knowledge.parsers import (
    ExtractResult,
    assert_supported,
    extract_text_from_file,
    sniff_extension,
)
from api.services.knowledge.qdrant_store import get_qdrant_store
from common.config import get_settings
from common.errors import AppError, ErrorCode
from common.times import to_epoch_ms
from db.models.knowledge import KnowledgeDocument
from db.session import AsyncSessionLocal

logger = logging.getLogger("api.kb.ingest")


def short_id(n: int = 10) -> str:
    return secrets.token_hex(n)[:n]


def _storage_root() -> Path:
    return Path(get_settings().storage_root).expanduser().resolve()


def resolve_storage_path(key: str) -> Path:
    rel = (key or "").replace("\\", "/").lstrip("/")
    if not rel or ".." in Path(rel).parts:
        raise AppError(ErrorCode.BAD_REQUEST, "invalid storage key", status_code=400)
    root = _storage_root()
    path = (root / rel).resolve()
    if not path.is_relative_to(root):
        raise AppError(ErrorCode.BAD_REQUEST, "invalid storage key", status_code=400)
    return path


def unlink_stored_file(key: str | None) -> None:
    if not key:
        return
    try:
        path = resolve_storage_path(key)
    except AppError:
        return
    if path.is_file():
        path.unlink(missing_ok=True)


def unlink_base_storage(base_public_id: str, docs: list[KnowledgeDocument] | None = None) -> None:
    import shutil

    for doc in docs or []:
        unlink_stored_file(doc.file_key or doc.storage_path)
        unlink_stored_file(_extracted_text_key(base_public_id, doc.public_id))
    try:
        folder = resolve_storage_path(f"knowledge/{base_public_id}")
    except AppError:
        return
    if folder.is_dir():
        shutil.rmtree(folder, ignore_errors=True)


def _extracted_text_key(base_public_id: str, doc_public_id: str) -> str:
    return f"knowledge/{base_public_id}/{doc_public_id}.txt"


def write_extracted_text(base_public_id: str, doc_public_id: str, text: str) -> None:
    """解析正文旁路落盘，预览/重解析不依赖 Qdrant。"""
    path = resolve_storage_path(_extracted_text_key(base_public_id, doc_public_id))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def read_extracted_text(base_public_id: str, doc_public_id: str) -> str | None:
    try:
        path = resolve_storage_path(_extracted_text_key(base_public_id, doc_public_id))
    except AppError:
        return None
    if not path.is_file():
        return None
    raw = path.read_text(encoding="utf-8").strip()
    return raw or None


def _safe_upload_name(raw: str | None) -> str:
    name = Path(raw or "upload").name.strip().replace("\x00", "")
    return name or "upload"


async def _write_upload(upload: UploadFile, dest: Path, *, max_bytes: int) -> tuple[int, str]:
    dest.parent.mkdir(parents=True, exist_ok=True)
    size = 0
    digest = hashlib.sha256()
    try:
        with dest.open("wb") as fh:
            while True:
                chunk = await upload.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise AppError(
                        ErrorCode.VALIDATION,
                        f"文件超过大小上限（{max_bytes} 字节）",
                        status_code=422,
                    )
                digest.update(chunk)
                fh.write(chunk)
    except Exception:
        dest.unlink(missing_ok=True)
        raise
    if size <= 0:
        dest.unlink(missing_ok=True)
        raise AppError(ErrorCode.VALIDATION, "上传文件为空", status_code=422)
    return size, digest.hexdigest()


def _error_msg(exc: BaseException) -> str:
    if isinstance(exc, AppError):
        return (exc.msg or "").strip() or "入库失败"
    text = str(exc).strip()
    return text or exc.__class__.__name__


def _has_original_file(doc: KnowledgeDocument) -> bool:
    key = doc.file_key or doc.storage_path
    if not key:
        return False
    try:
        path = resolve_storage_path(key)
    except AppError:
        return False
    return path.is_file()


def _apply_extract_stats(doc: KnowledgeDocument, extracted: ExtractResult) -> None:
    doc.page_count = int(extracted.page_count or 0)
    doc.ocr_pages = int(extracted.ocr_pages or 0)
    doc.ocr_capped = bool(extracted.ocr_capped)
    doc.formula_fallback = bool(extracted.formula_fallback)


async def _find_by_content_hash(
    db: AsyncSession, *, base_id: int, digest: str
) -> KnowledgeDocument | None:
    if not digest:
        return None
    result = await db.execute(
        select(KnowledgeDocument)
        .where(
            and_(
                KnowledgeDocument.base_id == base_id,
                KnowledgeDocument.content_hash == digest,
            )
        )
        .order_by(KnowledgeDocument.id.desc())
    )
    return result.scalars().first()


def _as_duplicate(doc: KnowledgeDocument, *, base_public_id: str) -> dict[str, Any]:
    setattr(doc, "_base_public_id", base_public_id)
    setattr(doc, "_duplicate", True)
    return to_kb_item(doc)


def to_kb_item(doc: KnowledgeDocument) -> dict[str, Any]:
    created_ms = to_epoch_ms(doc.created_at)
    file_key = doc.file_key or doc.storage_path
    return {
        "id": doc.public_id,
        "baseId": getattr(doc, "_base_public_id", None),
        "name": doc.name,
        "source": doc.source,
        "kind": doc.kind,
        "parentId": getattr(doc, "parent_id", None),
        "parentName": getattr(doc, "_parent_name", None),
        "fileType": doc.file_type,
        "size": doc.size,
        "url": doc.url,
        "fileKey": file_key,
        "previewUrl": doc.preview_url,
        "summary": doc.summary,
        "charCount": doc.char_count,
        "chunks": doc.chunk_count,
        "tags": doc.tags or [],
        "uploader": doc.uploader,
        "status": doc.status,
        "errorMsg": doc.error_msg,
        "pageCount": int(getattr(doc, "page_count", 0) or 0),
        "ocrPages": int(getattr(doc, "ocr_pages", 0) or 0),
        "ocrCapped": bool(getattr(doc, "ocr_capped", False)),
        "formulaFallback": bool(getattr(doc, "formula_fallback", False)),
        "reviewStatus": getattr(doc, "review_status", None) or "approved",
        "reviewComment": getattr(doc, "review_comment", None),
        "taskId": getattr(doc, "_task_id", None),
        "duplicate": bool(getattr(doc, "_duplicate", False)),
        "createdAt": created_ms,
        "createdAtUtc": True,
    }


async def _enqueue_unindexed_ready(
    db: AsyncSession,
    base: Any,
    docs: list[KnowledgeDocument],
) -> None:
    """已解析但未切块的资料（含历史待审核）补进向量，上传即用。"""
    pending = [
        d
        for d in docs
        if (d.status or "") == "ready"
        and (d.kind or "") != "drawing"
        and (d.review_status or "") != "rejected"
        and int(d.chunk_count or 0) == 0
    ]
    stamped = False
    still: list[KnowledgeDocument] = []
    for doc in pending:
        base_pid = str(getattr(base, "public_id", "") or "")
        sidecar = read_extracted_text(base_pid, doc.public_id) if base_pid else ""
        sidecar = sidecar or ""
        body = sidecar if sidecar and not sidecar.startswith("PERFJSON:") else ""
        verdict = classify_kb_document(name=doc.name or "", text=body)
        if verdict.skip_vectorize:
            if not has_skip_rag_tag(doc.tags):
                doc.tags = apply_skip_rag_tag(doc.tags)
                stamped = True
            continue
        if has_skip_rag_tag(doc.tags):
            doc.tags = [str(t) for t in (doc.tags or []) if str(t).strip() != TAG_BID_FOREIGN]
            stamped = True
        still.append(doc)
    if stamped:
        await db.commit()
    pending = still
    if not pending:
        return
    from api.services.knowledge.queue import enqueue_ingest_task
    from db.models.knowledge import KnowledgeIngestTask

    busy_result = await db.execute(
        select(KnowledgeIngestTask.document_id).where(
            KnowledgeIngestTask.document_id.in_([d.id for d in pending]),
            KnowledgeIngestTask.status.in_(("queued", "running")),
        )
    )
    busy = {int(x) for x in busy_result.scalars().all() if x is not None}
    for doc in pending:
        if doc.id in busy:
            continue
        doc.review_status = "approved"
        doc.status = "parsing"
        doc.summary = doc.summary or "正在写入检索…"
        await enqueue_ingest_task(db, base=base, doc=doc, force_reextract=False)


async def list_documents(
    db: AsyncSession,
    *,
    base_public_id: str,
    review_status: str | None = None,
) -> list[dict[str, Any]]:
    base = await get_base_by_public_id(db, base_public_id)
    stmt = select(KnowledgeDocument).where(KnowledgeDocument.base_id == base.id)
    rs = (review_status or "").strip().lower()
    if rs in {"pending", "approved", "rejected"}:
        stmt = stmt.where(KnowledgeDocument.review_status == rs)
    result = await db.execute(stmt.order_by(KnowledgeDocument.created_at.desc()))
    docs = result.scalars().all()
    await _enqueue_unindexed_ready(db, base, list(docs))
    names = {d.public_id: d.name for d in docs}
    items: list[dict[str, Any]] = []
    for d in docs:
        setattr(d, "_base_public_id", base.public_id)
        pid = getattr(d, "parent_id", None)
        setattr(d, "_parent_name", names.get(pid) if pid else None)
        items.append(to_kb_item(d))
    return items


def _normalize_doc_name(name: str) -> str:
    return Path(name or "").name.strip().lower()


async def find_duplicate_documents(
    db: AsyncSession,
    *,
    base_public_id: str,
    name: str,
    url: str | None = None,
) -> list[dict[str, Any]]:
    """同库内按资料名称（忽略大小写）或 URL 判重。"""
    base = await get_base_by_public_id(db, base_public_id)
    result = await db.execute(
        select(KnowledgeDocument).where(KnowledgeDocument.base_id == base.id)
    )
    norm_name = _normalize_doc_name(name)
    url_key = (url or "").strip().lower()
    out: list[dict[str, Any]] = []
    for doc in result.scalars().all():
        matched = norm_name and _normalize_doc_name(doc.name) == norm_name
        if not matched and url_key and doc.url:
            matched = doc.url.strip().lower() == url_key
        if matched:
            setattr(doc, "_base_public_id", base.public_id)
            out.append(to_kb_item(doc))
    out.sort(key=lambda x: int(x.get("createdAt") or 0), reverse=True)
    return out


async def delete_document_record(
    db: AsyncSession,
    *,
    base_public_id: str,
    doc_public_id: str,
) -> dict[str, Any] | None:
    """删除单条资料：向量、磁盘原文件、数据库记录（不 commit）。"""
    base = await get_base_by_public_id(db, base_public_id)
    result = await db.execute(
        select(KnowledgeDocument).where(
            KnowledgeDocument.public_id == doc_public_id,
            KnowledgeDocument.base_id == base.id,
        )
    )
    doc = result.scalar_one_or_none()
    if doc is None:
        return None
    children_result = await db.execute(
        select(KnowledgeDocument).where(
            KnowledgeDocument.base_id == base.id,
            KnowledgeDocument.parent_id == doc.public_id,
        )
    )
    children = list(children_result.scalars().all())
    store = get_qdrant_store()
    setattr(doc, "_base_public_id", base.public_id)
    deleted = to_kb_item(doc)
    for item in [*children, doc]:
        try:
            store.delete_by_doc_id(item.public_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("delete vectors failed id=%s: %s", item.public_id, exc)
        unlink_stored_file(item.file_key or item.storage_path)
        unlink_stored_file(_extracted_text_key(base.public_id, item.public_id))
        await db.delete(item)
    await db.flush()
    return deleted


async def delete_duplicate_documents(
    db: AsyncSession,
    *,
    base_public_id: str,
    name: str,
    url: str | None = None,
) -> list[dict[str, Any]]:
    dupes = await find_duplicate_documents(
        db, base_public_id=base_public_id, name=name, url=url
    )
    deleted: list[dict[str, Any]] = []
    for item in dupes:
        row = await delete_document_record(
            db, base_public_id=base_public_id, doc_public_id=str(item["id"])
        )
        if row:
            deleted.append(row)
    return deleted


async def ensure_upload_allowed(
    db: AsyncSession,
    *,
    base_public_id: str,
    name: str,
    url: str | None = None,
    force: bool = False,
) -> list[dict[str, Any]]:
    """未 force 时同名/同 URL 抛 409；force 时先删旧版再允许上传。"""
    dupes = await find_duplicate_documents(
        db, base_public_id=base_public_id, name=name, url=url
    )
    if not dupes:
        return []
    if force:
        return await delete_duplicate_documents(
            db, base_public_id=base_public_id, name=name, url=url
        )
    first = dupes[0]
    hint = f"（{first.get('uploader') or '未知上传者'}）" if first.get("uploader") else ""
    raise AppError(
        ErrorCode.CONFLICT,
        f"资料「{first.get('name') or name}」已存在于当前知识库{hint}，请勿重复上传。"
        f"若确需覆盖请确认继续上传。",
        status_code=409,
    )


async def require_embedding_ready(db: AsyncSession) -> None:
    await get_embedding_client(db)


def _initial_review_status(*, drawing: bool) -> str:
    del drawing
    return "approved"


async def ingest_upload(
    db: AsyncSession,
    *,
    base_public_id: str,
    upload: UploadFile,
    name: str | None = None,
    tags: list[str] | None = None,
    uploader: str | None = None,
    created_by: int | None = None,
    parent_id: str | None = None,
    as_attachment: bool | None = None,
) -> dict[str, Any]:
    """落盘原文件。图纸附件不 OCR、不进向量；其余后台抽字/向量化。"""
    settings = get_settings()
    filename = _safe_upload_name(upload.filename)
    ext = assert_supported(sniff_extension(filename, upload.content_type))
    base = await get_base_by_public_id(db, base_public_id)
    parent_pid = (parent_id or "").strip() or None
    tag_list = list(tags) if tags is not None else ["手动上传"]
    drawing = _want_drawing_attachment(
        ext=ext,
        as_attachment=as_attachment,
        parent_id=parent_pid,
        tags=tag_list,
    )
    if drawing and "图纸附件" not in tag_list:
        tag_list.append("图纸附件")

    if not drawing:
        await require_embedding_ready(db)
    public_id = short_id(12)
    rel_key = f"knowledge/{base_public_id}/{public_id}.{ext}"
    dest = resolve_storage_path(rel_key)
    _size, digest = await _write_upload(upload, dest, max_bytes=int(settings.kb_upload_max_bytes))

    existing = await _find_by_content_hash(db, base_id=base.id, digest=digest)
    if existing is not None:
        dest.unlink(missing_ok=True)
        if existing.status == "failed":
            return await reparse_document(
                db, base_public_id=base_public_id, doc_public_id=existing.public_id
            )
        return _as_duplicate(existing, base_public_id=base.public_id)

    if parent_pid:
        parent = await _get_doc_in_base(db, base_id=base.id, public_id=parent_pid)
        if parent is None:
            dest.unlink(missing_ok=True)
            raise AppError(ErrorCode.VALIDATION, "parentId 对应的案例文档不存在", status_code=422)

    display_name = (name or "").strip() or Path(filename).stem or filename
    doc = KnowledgeDocument(
        public_id=public_id,
        base_id=base.id,
        name=display_name,
        source="file",
        kind="drawing" if drawing else "doc",
        parent_id=parent_pid,
        file_type=ext,
        size=dest.stat().st_size,
        storage_path=rel_key,
        file_key=rel_key,
        summary="图纸附件（未向量化）" if drawing else "正在解析…",
        char_count=0,
        chunk_count=0,
        content_hash=digest,
        tags=tag_list,
        uploader=uploader,
        status="ready" if drawing else "parsing",
        review_status=_initial_review_status(drawing=drawing),
        created_by=created_by,
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)
    setattr(doc, "_base_public_id", base.public_id)
    if not drawing:
        from api.services.knowledge.queue import enqueue_ingest_task

        task = await enqueue_ingest_task(db, base=base, doc=doc)
        setattr(doc, "_task_id", task.public_id)
    return to_kb_item(doc)


def _want_drawing_attachment(
    *,
    ext: str,
    as_attachment: bool | None,
    parent_id: str | None,
    tags: list[str],
) -> bool:
    if as_attachment is True:
        return is_drawing_ext(ext)
    if as_attachment is False:
        return False
    if parent_id:
        return is_drawing_ext(ext)
    lowered = {str(t).strip() for t in tags}
    if lowered & {"图纸附件", "图纸", "布置图", "drawing"}:
        return is_drawing_ext(ext)
    return False


async def _get_doc_in_base(
    db: AsyncSession, *, base_id: int, public_id: str
) -> KnowledgeDocument | None:
    result = await db.execute(
        select(KnowledgeDocument).where(
            KnowledgeDocument.base_id == base_id,
            KnowledgeDocument.public_id == public_id,
        )
    )
    return result.scalar_one_or_none()


async def process_uploaded_document(public_id: str, *, force_reextract: bool = False) -> None:
    """后台：解析（含 OCR）后立刻向量化，上传完成即可检索。"""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(KnowledgeDocument).where(KnowledgeDocument.public_id == public_id)
        )
        doc = result.scalar_one_or_none()
        if doc is None:
            logger.warning("ingest background: doc missing %s", public_id)
            return
        from db.models.knowledge import KnowledgeBase

        base_row = await db.execute(select(KnowledgeBase).where(KnowledgeBase.id == doc.base_id))
        base = base_row.scalar_one_or_none()
        base_pid = base.public_id if base else ""
        try:
            if (doc.kind or "") == "drawing":
                doc.status = "ready"
                doc.review_status = "approved"
                doc.summary = doc.summary or "图纸附件（未向量化）"
                doc.chunk_count = 0
                await db.commit()
                return
            extracted = await _load_or_extract_text(
                doc, base_pid, prefer_cached=not force_reextract
            )
            text = extracted.text
            if extracted.from_file:
                _apply_extract_stats(doc, extracted)
            if base_pid:
                write_extracted_text(base_pid, doc.public_id, text)
            if not (doc.summary or "").startswith("PERFJSON:"):
                doc.summary = text[:200]
            doc.char_count = len(text)
            doc.chunk_count = 0
            doc.status = "ready"
            doc.error_msg = None
            doc.review_status = "approved"
            await _vectorize_document(db, doc, text)
            await db.commit()
            logger.info(
                "ingest background ready id=%s review=%s ocr_pages=%s",
                public_id,
                doc.review_status,
                doc.ocr_pages,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("ingest background failed id=%s", public_id)
            doc.status = "failed"
            doc.error_msg = _error_msg(exc)
            await db.commit()


async def _load_or_extract_text(
    doc: KnowledgeDocument,
    base_public_id: str,
    *,
    prefer_cached: bool = True,
) -> ExtractResult:
    """有旁路正文则复用（避免向量化失败时再花一次 OCR）；否则从原文件解析。"""
    cached = read_extracted_text(base_public_id, doc.public_id) if base_public_id else None
    if prefer_cached and cached:
        return ExtractResult(text=cached, from_file=False)
    key = doc.file_key or doc.storage_path
    if key:
        try:
            path = resolve_storage_path(key)
        except AppError:
            path = None
        if path is not None and path.is_file():
            return await extract_text_from_file(path, ext=doc.file_type)
    if cached:
        return ExtractResult(text=cached, from_file=False)
    raise AppError(ErrorCode.VALIDATION, "无原文件且无解析正文，无法重解析", status_code=422)


async def fail_stale_parsing_documents() -> int:
    """启动时收回入库队列，并把没有任务的 parsing 文档标失败。"""
    from api.services.knowledge.queue import reclaim_stale_tasks

    return await reclaim_stale_tasks()


async def reparse_document(
    db: AsyncSession,
    *,
    base_public_id: str,
    doc_public_id: str,
) -> dict[str, Any]:
    """失败/已就绪文档重新解析：先删旧向量，再后台入库。"""
    base = await get_base_by_public_id(db, base_public_id)
    result = await db.execute(
        select(KnowledgeDocument).where(
            KnowledgeDocument.public_id == doc_public_id,
            KnowledgeDocument.base_id == base.id,
        )
    )
    doc = result.scalar_one_or_none()
    if doc is None:
        raise AppError(ErrorCode.NOT_FOUND, "document not found", status_code=404)
    if doc.status == "parsing":
        raise AppError(ErrorCode.CONFLICT, "文档正在解析，请稍候", status_code=409)

    if (doc.kind or "") == "drawing":
        doc.status = "ready"
        doc.review_status = "approved"
        doc.error_msg = None
        doc.summary = "图纸附件（未向量化）"
        doc.chunk_count = 0
        await db.commit()
        await db.refresh(doc)
        setattr(doc, "_base_public_id", base.public_id)
        return to_kb_item(doc)

    try:
        get_qdrant_store().delete_by_doc_id(doc.public_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("reparse delete vectors failed id=%s: %s", doc.public_id, exc)
    force_reextract = _has_original_file(doc)
    if force_reextract:
        unlink_stored_file(_extracted_text_key(base.public_id, doc.public_id))
    doc.status = "parsing"
    doc.review_status = "approved"
    doc.error_msg = None
    doc.summary = "正在重新解析…"
    doc.chunk_count = 0
    doc.char_count = 0
    doc.page_count = 0
    doc.ocr_pages = 0
    doc.ocr_capped = False
    doc.formula_fallback = False
    await db.commit()
    await db.refresh(doc)
    setattr(doc, "_base_public_id", base.public_id)
    from api.services.knowledge.queue import enqueue_ingest_task

    task = await enqueue_ingest_task(db, base=base, doc=doc, force_reextract=force_reextract)
    setattr(doc, "_task_id", task.public_id)
    return to_kb_item(doc)


async def attach_document(
    db: AsyncSession,
    *,
    base_public_id: str,
    doc_public_id: str,
    parent_id: str | None,
    as_attachment: bool | None = True,
) -> dict[str, Any]:
    """把 PDF/图片挂到案例卡。默认同时改为图纸附件并删掉已写入的向量。"""
    base = await get_base_by_public_id(db, base_public_id)
    doc = await _get_doc_in_base(db, base_id=base.id, public_id=doc_public_id)
    if doc is None:
        raise AppError(ErrorCode.NOT_FOUND, "document not found", status_code=404)
    if doc.status == "parsing":
        raise AppError(ErrorCode.CONFLICT, "文档正在解析，请稍候", status_code=409)

    parent_pid = (parent_id or "").strip() or None
    if parent_pid:
        if parent_pid == doc.public_id:
            raise AppError(ErrorCode.VALIDATION, "不能挂到自己", status_code=422)
        parent = await _get_doc_in_base(db, base_id=base.id, public_id=parent_pid)
        if parent is None:
            raise AppError(ErrorCode.VALIDATION, "parentId 对应的案例文档不存在", status_code=422)
        if (parent.kind or "") == "drawing":
            raise AppError(ErrorCode.VALIDATION, "不能挂到图纸附件上", status_code=422)

    become_drawing = as_attachment is not False and is_drawing_ext(doc.file_type)
    if as_attachment is True and not is_drawing_ext(doc.file_type):
        raise AppError(ErrorCode.VALIDATION, "只有 PDF/图片才能作为图纸附件", status_code=422)

    if become_drawing:
        try:
            get_qdrant_store().delete_by_doc_id(doc.public_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("attach delete vectors failed id=%s: %s", doc.public_id, exc)
        doc.kind = "drawing"
        doc.review_status = "approved"
        doc.chunk_count = 0
        doc.ocr_pages = 0
        doc.ocr_capped = False
        doc.summary = "图纸附件（未向量化）"
        tags = [str(t) for t in (doc.tags or [])]
        if "图纸附件" not in tags:
            tags.append("图纸附件")
        doc.tags = tags
        doc.status = "ready"
        doc.error_msg = None

    doc.parent_id = parent_pid
    await db.commit()
    await db.refresh(doc)
    setattr(doc, "_base_public_id", base.public_id)
    if parent_pid:
        parent = await _get_doc_in_base(db, base_id=base.id, public_id=parent_pid)
        setattr(doc, "_parent_name", parent.name if parent is not None else None)
    return to_kb_item(doc)


async def ingest_text(
    db: AsyncSession,
    *,
    base_public_id: str,
    name: str,
    content: str,
    source: str = "text",
    file_type: str | None = "txt",
    size: int | None = None,
    url: str | None = None,
    file_key: str | None = None,
    preview_url: str | None = None,
    kind: str = "doc",
    tags: list[str] | None = None,
    uploader: str | None = None,
    created_by: int | None = None,
    public_id: str | None = None,
) -> dict[str, Any]:
    text = content.strip()
    if len(text) < 4:
        raise AppError(ErrorCode.VALIDATION, "content too short", status_code=422)

    if kind != "drawing":
        await require_embedding_ready(db)

    base = await get_base_by_public_id(db, base_public_id)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    existing = await _find_by_content_hash(db, base_id=base.id, digest=digest)
    if existing is not None and existing.status != "failed":
        return _as_duplicate(existing, base_public_id=base.public_id)
    if existing is not None and existing.status == "failed":
        write_extracted_text(base.public_id, existing.public_id, text)
        return await reparse_document(
            db, base_public_id=base_public_id, doc_public_id=existing.public_id
        )

    public_id = (public_id or "").strip() or short_id(12)
    tag_list = list(tags or [])
    if is_layout_case_card(text) and "布置案例" not in tag_list:
        tag_list.append("布置案例")
    doc = KnowledgeDocument(
        public_id=public_id,
        base_id=base.id,
        name=name.strip() or "untitled",
        source=source,
        kind=kind,
        file_type=file_type,
        size=size if size is not None else len(text.encode("utf-8")),
        url=url,
        storage_path=file_key,
        file_key=file_key,
        preview_url=preview_url,
        summary=text[:200],
        char_count=len(text),
        chunk_count=0,
        content_hash=digest,
        tags=tag_list,
        uploader=uploader,
        status="parsing",
        review_status=_initial_review_status(drawing=(kind == "drawing")),
        created_by=created_by,
    )
    db.add(doc)
    await db.flush()

    try:
        write_extracted_text(base.public_id, public_id, text)
        if kind == "drawing":
            doc.status = "ready"
            doc.review_status = "approved"
            doc.summary = text[:200]
            doc.char_count = len(text)
            doc.chunk_count = 0
            doc.error_msg = None
        else:
            doc.summary = text[:200]
            doc.char_count = len(text)
            doc.chunk_count = 0
            doc.status = "ready"
            doc.review_status = "approved"
            doc.error_msg = None
            await _vectorize_document(db, doc, text)
    except Exception as exc:  # noqa: BLE001
        doc.status = "failed"
        doc.error_msg = _error_msg(exc)
        await db.commit()
        await db.refresh(doc)
        if isinstance(exc, AppError):
            raise
        raise AppError(ErrorCode.INTERNAL, f"ingest failed: {exc}", status_code=500) from exc

    await db.commit()
    await db.refresh(doc)
    setattr(doc, "_base_public_id", base.public_id)
    return to_kb_item(doc)


def _apply_skip_rag(doc: KnowledgeDocument, reason: str) -> None:
    doc.tags = apply_skip_rag_tag(doc.tags)
    doc.chunk_count = 0
    doc.status = "ready"
    doc.error_msg = None
    summary = doc.summary or ""
    if not summary.startswith("PERFJSON:"):
        doc.summary = (reason or "未向量化，检索将跳过")[:200]


async def _vectorize_document(db: AsyncSession, doc: KnowledgeDocument, text: str) -> None:
    from db.models.knowledge import KnowledgeBase

    cleaned = text.strip()
    if len(cleaned) < 4:
        raise AppError(ErrorCode.VALIDATION, "content too short", status_code=422)

    verdict = classify_kb_document(name=doc.name or "", text=cleaned, tags=doc.tags)
    if verdict.skip_vectorize:
        try:
            get_qdrant_store().delete_by_doc_id(doc.public_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("skip-rag delete vectors failed id=%s: %s", doc.public_id, exc)
        _apply_skip_rag(doc, verdict.reason)
        return

    if has_skip_rag_tag(doc.tags):
        doc.tags = [str(t) for t in (doc.tags or []) if str(t).strip() != TAG_BID_FOREIGN]

    result = await db.execute(select(KnowledgeBase).where(KnowledgeBase.id == doc.base_id))
    base = result.scalar_one_or_none()
    if base is None:
        raise AppError(ErrorCode.NOT_FOUND, "knowledge base not found", status_code=404)

    chunks = split_text(cleaned)
    if not chunks:
        raise AppError(ErrorCode.VALIDATION, "content too short after chunk", status_code=422)

    embedder = await get_embedding_client(db)
    vectors = await embedder.embed_documents(chunks)
    store = get_qdrant_store()
    store.delete_by_doc_id(doc.public_id)
    store.upsert_chunks(
        public_id=doc.public_id,
        kb_id=base.public_id,
        name=doc.name,
        source=doc.source,
        tags=doc.tags if isinstance(doc.tags, list) else [],
        chunks=chunks,
        vectors=vectors,
    )
    doc.char_count = len(cleaned)
    doc.chunk_count = len(chunks)
    # 业绩合同的 PERFJSON 摘要供标书生成用，不能被 OCR 正文覆盖
    if not (doc.summary or "").startswith("PERFJSON:"):
        doc.summary = cleaned[:200]
    doc.status = "ready"
    doc.error_msg = None


async def get_document_preview(
    db: AsyncSession,
    *,
    base_public_id: str,
    doc_public_id: str,
) -> dict[str, Any]:
    """返回文档元数据 + 向量库文本块拼接预览。"""
    base = await get_base_by_public_id(db, base_public_id)
    result = await db.execute(
        select(KnowledgeDocument).where(
            KnowledgeDocument.public_id == doc_public_id,
            KnowledgeDocument.base_id == base.id,
        )
    )
    doc = result.scalar_one_or_none()
    if doc is None:
        raise AppError(ErrorCode.NOT_FOUND, "document not found", status_code=404)

    setattr(doc, "_base_public_id", base.public_id)
    chunks: list[dict[str, Any]] = []
    try:
        chunks = get_qdrant_store().list_chunks_by_doc_id(doc_public_id)
    except Exception:  # noqa: BLE001
        chunks = []

    sidecar = read_extracted_text(base.public_id, doc_public_id)
    parts = [str(c.get("content") or "") for c in chunks if str(c.get("content") or "").strip()]
    qdrant_text = "\n\n".join(parts).strip()
    truncated = False
    if sidecar:
        content = sidecar
    elif qdrant_text:
        content = qdrant_text
    elif doc.summary:
        content = doc.summary
        truncated = True
    else:
        content = ""

    return {
        "item": to_kb_item(doc),
        "chunks": chunks,
        "content": content,
        "truncated": truncated,
    }


async def download_document(
    db: AsyncSession,
    *,
    base_public_id: str,
    doc_public_id: str,
) -> tuple[bytes, str, str]:
    """优先磁盘原文件；否则导出预览正文为 txt。"""
    base = await get_base_by_public_id(db, base_public_id)
    result = await db.execute(
        select(KnowledgeDocument).where(
            KnowledgeDocument.public_id == doc_public_id,
            KnowledgeDocument.base_id == base.id,
        )
    )
    doc = result.scalar_one_or_none()
    if doc is None:
        raise AppError(ErrorCode.NOT_FOUND, "document not found", status_code=404)

    key = doc.file_key or doc.storage_path
    if key:
        try:
            path = resolve_storage_path(key)
        except AppError:
            path = None
        if path is not None and path.is_file():
            name = path.name or f"{doc.name}.bin"
            suffix = path.suffix.lower()
            media = {
                ".txt": "text/plain; charset=utf-8",
                ".md": "text/markdown; charset=utf-8",
                ".csv": "text/csv; charset=utf-8",
                ".json": "application/json",
                ".xml": "application/xml",
                ".yaml": "text/yaml",
                ".yml": "text/yaml",
                ".xls": "application/vnd.ms-excel",
                ".pdf": "application/pdf",
                ".docx": (
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                ),
                ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".webp": "image/webp",
                ".gif": "image/gif",
                ".bmp": "image/bmp",
                ".mp4": "video/mp4",
            }.get(suffix, "application/octet-stream")
            return path.read_bytes(), name, media

    preview = await get_document_preview(
        db, base_public_id=base_public_id, doc_public_id=doc_public_id
    )
    content = str(preview.get("content") or doc.summary or doc.name or "")
    if not content.strip():
        raise AppError(40402, "文档无可导出内容", status_code=404)
    filename = f"{doc.name or doc.public_id}.txt".replace("/", "_").replace("\\", "_")
    return content.encode("utf-8"), filename, "text/plain; charset=utf-8"


async def search_chunks(
    db: AsyncSession,
    *,
    query: str,
    top_k: int = 5,
    min_score: float = 0.0,
    kb_id: str | None = None,
    kb_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    from api.services.knowledge.rerank import (
        compact_search_query,
        extract_lookup_needles,
        filter_weak_hits,
        hybrid_rerank,
    )

    q = compact_search_query(query)
    if not q:
        raise AppError(ErrorCode.VALIDATION, "query is required", status_code=422)

    settings = get_settings()
    embedder = await get_embedding_client(db)
    vector = await embedder.embed_query(q)
    store = get_qdrant_store()

    mult = max(1, int(settings.kb_search_candidate_multiplier or 1))
    candidate_k = max(top_k, top_k * mult)
    hits = store.search(
        vector=vector,
        top_k=candidate_k,
        min_score=min_score,
        kb_id=kb_id,
        kb_ids=kb_ids,
    )
    needles = extract_lookup_needles(q)
    if needles:
        lexical = store.search_text_contains(
            needles=needles,
            kb_id=kb_id,
            kb_ids=kb_ids,
            limit=max(8, top_k),
        )
        merged: dict[tuple[str, int], dict[str, Any]] = {}
        for item in [*lexical, *hits]:
            key = (str(item.get("doc_id") or ""), int(item.get("chunk_index") or 0))
            prev = merged.get(key)
            if prev is None or float(item.get("score") or 0) > float(prev.get("score") or 0):
                merged[key] = item
        hits = list(merged.values())
    hits = await _filter_approved_hits(db, hits)
    ranked = hybrid_rerank(
        q,
        hits,
        top_k=top_k,
        keyword_weight=float(settings.kb_search_keyword_weight),
    )
    ranked = filter_weak_hits(
        ranked,
        floor=float(settings.kb_score_floor),
        keep_ratio=float(settings.kb_keep_ratio),
    )
    window = int(settings.kb_neighbor_window or 0)
    if window > 0 and ranked:
        try:
            neighbors = store.fetch_neighbor_chunks(ranked, window=window)
        except Exception as exc:  # noqa: BLE001
            logger.warning("neighbor expand failed: %s", exc)
            neighbors = []
        merged_neighbors: dict[tuple[str, int], dict[str, Any]] = {}
        for item in [*ranked, *neighbors]:
            key = (str(item.get("doc_id") or ""), int(item.get("chunk_index") or 0))
            prev = merged_neighbors.get(key)
            if prev is None or float(item.get("score") or 0) > float(prev.get("score") or 0):
                merged_neighbors[key] = item
        ranked = sorted(
            merged_neighbors.values(),
            key=lambda x: float(x.get("score") or 0.0),
            reverse=True,
        )[: max(top_k + window * 2, top_k)]
    return await _fill_chunk_names(db, ranked)


async def _filter_approved_hits(
    db: AsyncSession, hits: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    ids = [str(h.get("doc_id") or "").strip() for h in hits]
    ids = [x for x in ids if x]
    if not ids:
        return hits
    result = await db.execute(
        select(KnowledgeDocument.public_id, KnowledgeDocument.tags).where(
            KnowledgeDocument.public_id.in_(ids),
            KnowledgeDocument.status == "ready",
            KnowledgeDocument.review_status == "approved",
        )
    )
    allowed = {
        str(pid)
        for pid, tags in result.all()
        if not has_skip_rag_tag(tags)
    }
    return [
        h
        for h in hits
        if str(h.get("doc_id") or "").strip() in allowed
        and not has_skip_rag_tag(h.get("tags"))
    ]


async def search_knowledge_docs(
    db: AsyncSession,
    *,
    query: str,
    top_k: int = 3,
    min_score: float = 0.0,
    kb_id: str | None = None,
    kb_ids: list[str] | None = None,
    max_drawings: int = 2,
) -> list[dict[str, Any]]:
    """召回后按文档折叠成完整案例（最多 top_k 篇），并附带最多 2 张图纸缩略图。"""
    doc_k = max(1, min(int(top_k), 8))
    hits = await search_chunks(
        db,
        query=query,
        top_k=max(8, doc_k * 4),
        min_score=min_score,
        kb_id=kb_id,
        kb_ids=kb_ids,
    )
    docs = await _collapse_hits_to_docs(db, hits, max_docs=doc_k)
    await _attach_case_drawings(db, docs, max_images=max(0, min(int(max_drawings), 2)))
    return docs


async def _collapse_hits_to_docs(
    db: AsyncSession, hits: list[dict[str, Any]], *, max_docs: int
) -> list[dict[str, Any]]:
    ordered: list[str] = []
    best: dict[str, float] = {}
    pieces: dict[str, list[str]] = {}
    kb_of: dict[str, str] = {}
    name_of: dict[str, str] = {}
    for h in hits:
        pid = str(h.get("doc_id") or "").strip()
        if not pid:
            continue
        if pid not in best:
            ordered.append(pid)
            best[pid] = 0.0
            pieces[pid] = []
        best[pid] = max(best[pid], float(h.get("score") or 0.0))
        text = str(h.get("content") or "").strip()
        if text:
            pieces[pid].append(text)
        if h.get("kb_id"):
            kb_of[pid] = str(h.get("kb_id"))
        if h.get("name"):
            name_of[pid] = str(h.get("name"))
    if not ordered:
        return []
    result = await db.execute(
        select(KnowledgeDocument).where(KnowledgeDocument.public_id.in_(ordered))
    )
    rows = {d.public_id: d for d in result.scalars().all()}
    out: list[dict[str, Any]] = []
    for pid in ordered:
        if len(out) >= max_docs:
            break
        doc = rows.get(pid)
        if doc is not None and (doc.kind or "") == "drawing":
            continue
        if doc is not None and has_skip_rag_tag(doc.tags):
            continue
        kb_id = kb_of.get(pid) or ""
        sidecar = read_extracted_text(kb_id, pid) if kb_id else None
        if sidecar and (is_layout_case_card(sidecar) or len(sidecar) <= 4000):
            content = sidecar
        elif sidecar:
            content = sidecar[:4000]
        else:
            content = "\n\n".join(pieces.get(pid) or [])
        if not content.strip():
            continue
        title = (doc.name if doc is not None else None) or name_of.get(pid) or "未命名资料"
        if should_skip_kb_retrieval(
            name=title,
            content=content,
            tags=doc.tags if doc is not None else None,
        ):
            continue
        out.append(
            {
                "doc_id": pid,
                "name": (doc.name if doc is not None else None) or name_of.get(pid) or "未命名资料",
                "content": content,
                "score": best.get(pid) or 0.0,
                "kind": (doc.kind if doc is not None else "doc") or "doc",
                "kb_id": kb_id,
                "images": [],
            }
        )
    return out


async def _attach_case_drawings(
    db: AsyncSession, cases: list[dict[str, Any]], *, max_images: int
) -> None:
    if max_images <= 0 or not cases:
        return
    from api.services.ai.chat_images import blobs_to_state_images

    used = 0
    for case in cases:
        case["images"] = []
        if used >= max_images:
            continue
        children = await _list_child_drawings(db, parent_id=str(case.get("doc_id") or ""))
        for child in children:
            if used >= max_images:
                break
            blob = _preview_drawing_bytes(child)
            if not blob:
                continue
            imgs = blobs_to_state_images([("image/jpeg", blob)])
            case["images"].extend(imgs)
            used += 1


async def _list_child_drawings(db: AsyncSession, *, parent_id: str) -> list[KnowledgeDocument]:
    pid = (parent_id or "").strip()
    if not pid:
        return []
    result = await db.execute(
        select(KnowledgeDocument)
        .where(
            KnowledgeDocument.parent_id == pid,
            KnowledgeDocument.kind == "drawing",
            KnowledgeDocument.status == "ready",
        )
        .order_by(KnowledgeDocument.created_at.asc())
    )
    return list(result.scalars().all())


def _preview_drawing_bytes(doc: KnowledgeDocument) -> bytes | None:
    key = doc.file_key or doc.storage_path
    if not key:
        return None
    try:
        path = resolve_storage_path(key)
    except AppError:
        return None
    if not path.is_file():
        return None
    try:
        return preview_jpeg_from_file(path, ext=doc.file_type)
    except Exception as exc:  # noqa: BLE001
        logger.warning("drawing preview failed id=%s: %s", doc.public_id, exc)
        return None


async def delete_document(
    db: AsyncSession, *, base_public_id: str, doc_public_id: str
) -> dict[str, Any]:
    deleted = await delete_document_record(
        db, base_public_id=base_public_id, doc_public_id=doc_public_id
    )
    if deleted is None:
        raise AppError(ErrorCode.NOT_FOUND, "document not found", status_code=404)
    await db.commit()
    items = await list_documents(db, base_public_id=base_public_id)
    return {"items": items, "deleted": deleted}


async def _fill_chunk_names(
    db: AsyncSession, hits: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """用 MySQL 资料名称覆盖/补全 Qdrant payload，聊天引用才带得上名字。"""
    ids = [str(h.get("doc_id") or "").strip() for h in hits]
    ids = [x for x in ids if x]
    if not ids:
        return hits
    result = await db.execute(
        select(KnowledgeDocument.public_id, KnowledgeDocument.name).where(
            KnowledgeDocument.public_id.in_(ids)
        )
    )
    names = {str(pid): name for pid, name in result.all()}
    out: list[dict[str, Any]] = []
    for h in hits:
        item = dict(h)
        pid = str(item.get("doc_id") or "").strip()
        if pid in names and names[pid]:
            item["name"] = names[pid]
        elif not str(item.get("name") or "").strip():
            item["name"] = "未命名资料"
        out.append(item)
    return out
