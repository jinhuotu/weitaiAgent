"""投标资料库：落在知识库（purpose=asset）里，资料项可增删改名。"""

from __future__ import annotations

import hashlib
import logging
import re
import shutil
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.services.knowledge.bases import PURPOSE_ASSET, create_base, get_base_by_public_id, short_id
from api.services.knowledge.drawings import preview_jpeg_from_file
from api.services.knowledge.ingest import (
    delete_document_record,
    require_embedding_ready,
    resolve_storage_path,
    unlink_stored_file,
)
from api.services.knowledge.qdrant_store import get_qdrant_store
from api.services.knowledge.queue import enqueue_ingest_task
from api.services.tenders.performance import (
    dump_perf_meta,
    extract_performance_from_path,
    is_weak_title,
    load_perf_meta,
    rank_performance_lines,
)
from api.services.tenders.placeholders import DEFAULT_SLOTS, TECH_DRAWING_KEY, collect_slots
from api.services.tenders.schema import PerformanceLine, PlaceholderItem
from api.services.tenders.slots import (
    _KEY_RE,
    _MAX_BYTES,
    _MAX_FILES_PER_SLOT,
    clear_slot,
    drawing_slot_status,
    list_slot_files,
    slots_root,
)
from common.errors import AppError, ErrorCode
from db.models.knowledge import KnowledgeBase, KnowledgeDocument

logger = logging.getLogger("api.tenders.library")

TENDER_LIB_PUBLIC_ID = "tenderlib01"
TENDER_LIB_NAME = "投标资料库"
TAG_MATERIAL = "投标资料"
TAG_SLOT_PREFIX = "slot:"
_ALLOWED_EXT = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
_IMAGE_EXT = {"png", "jpg", "jpeg", "webp", "gif", "bmp"}
_FILE_MIME = {
    "pdf": "application/pdf",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "gif": "image/gif",
    "bmp": "image/bmp",
}


def library_file_kind(ext: str | None) -> str:
    kind = (ext or "").lower().lstrip(".")
    if kind == "pdf":
        return "pdf"
    if kind in _IMAGE_EXT:
        return "image"
    return "file"


def slot_tag(key: str) -> str:
    return f"{TAG_SLOT_PREFIX}{key}"


def key_from_tags(tags: list | None) -> str:
    for raw in tags or []:
        text = str(raw or "").strip()
        if text.startswith(TAG_SLOT_PREFIX):
            return text[len(TAG_SLOT_PREFIX) :].strip()
    return ""


def slug_key(title: str, public_id: str) -> str:
    ascii_part = re.sub(r"[^a-zA-Z0-9]+", "_", title or "").strip("_").lower()
    if ascii_part and _KEY_RE.match(ascii_part):
        return ascii_part[:64]
    return f"m{public_id}"[:64]


def _is_material_parent(doc: KnowledgeDocument) -> bool:
    if (doc.parent_id or "").strip():
        return False
    tags = [str(t) for t in (doc.tags or [])]
    return TAG_MATERIAL in tags or bool(key_from_tags(tags))


def _doc_file_path(doc: KnowledgeDocument) -> Path | None:
    key = doc.file_key or doc.storage_path
    if not key:
        return None
    try:
        path = resolve_storage_path(key)
    except AppError:
        return None
    return path if path.is_file() else None


async def ensure_library_base(
    db: AsyncSession,
    *,
    created_by: int | None = None,
) -> KnowledgeBase:
    result = await db.execute(
        select(KnowledgeBase).where(KnowledgeBase.public_id == TENDER_LIB_PUBLIC_ID)
    )
    base = result.scalar_one_or_none()
    if base is None:
        named = await db.execute(
            select(KnowledgeBase).where(
                KnowledgeBase.purpose == PURPOSE_ASSET,
                KnowledgeBase.name == TENDER_LIB_NAME,
                KnowledgeBase.status != "deleted",
            )
        )
        base = named.scalar_one_or_none()
    if base is None:
        await create_base(
            db,
            name=TENDER_LIB_NAME,
            description="公司投标常备扫描件，生成投标文件时按名称引用。",
            purpose=PURPOSE_ASSET,
            created_by=created_by,
            public_id=TENDER_LIB_PUBLIC_ID,
        )
        base = await get_base_by_public_id(db, TENDER_LIB_PUBLIC_ID)
    await _seed_defaults_if_empty(db, base)
    await _dedupe_duplicate_children(db, base)
    await _migrate_legacy_files(db, base)
    return base


async def _list_docs(db: AsyncSession, base_id: int) -> list[KnowledgeDocument]:
    result = await db.execute(
        select(KnowledgeDocument).where(KnowledgeDocument.base_id == base_id)
    )
    return list(result.scalars().all())


async def _seed_defaults_if_empty(db: AsyncSession, base: KnowledgeBase) -> None:
    docs = await _list_docs(db, base.id)
    existing = {key_from_tags(d.tags) for d in docs if _is_material_parent(d)}
    added = False
    for item in DEFAULT_SLOTS:
        if not item.key or item.key in existing:
            continue
        db.add(
            _new_parent(
                base_id=base.id,
                public_id=short_id(12),
                key=item.key,
                title=item.title,
                hint=item.hint or "",
            )
        )
        added = True
    if added:
        await db.commit()


def _new_parent(
    *,
    base_id: int,
    public_id: str,
    key: str,
    title: str,
    hint: str,
) -> KnowledgeDocument:
    note = (hint or title or "投标扫描件").strip() or "投标扫描件"
    if len(note) < 4:
        note = f"{note} 扫描件"
    return KnowledgeDocument(
        public_id=public_id,
        base_id=base_id,
        name=(title or key)[:255],
        source="text",
        kind="drawing",
        summary=note[:200],
        char_count=len(note),
        chunk_count=0,
        tags=[TAG_MATERIAL, slot_tag(key)],
        status="ready",
        review_status="approved",
    )


def _linked_hashes(parent: KnowledgeDocument, children: list[KnowledgeDocument]) -> set[str]:
    hashes = {str(c.content_hash) for c in children if c.content_hash}
    own = _doc_file_path(parent)
    if own and parent.content_hash:
        hashes.add(str(parent.content_hash))
    elif own:
        hashes.add(hashlib.sha256(own.read_bytes()).hexdigest())
    return hashes


def _already_has_file(hashes: set[str], src: Path) -> bool:
    digest = hashlib.sha256(src.read_bytes()).hexdigest()
    return digest in hashes


async def _dedupe_duplicate_children(db: AsyncSession, base: KnowledgeBase) -> None:
    """同一资料项下按文件哈希只留一份，清掉迁移时重复拷入的扫描件。"""
    docs = await _list_docs(db, base.id)
    removed = 0
    for children in _group_children(docs).values():
        seen: set[str] = set()
        for child in sorted(children, key=lambda d: (d.id or 0, d.public_id)):
            digest = str(child.content_hash or "").strip()
            if not digest:
                path = _doc_file_path(child)
                if path:
                    digest = hashlib.sha256(path.read_bytes()).hexdigest()
                    child.content_hash = digest
            if digest and digest in seen:
                unlink_stored_file(child.file_key or child.storage_path)
                await db.delete(child)
                removed += 1
                continue
            if digest:
                seen.add(digest)
    if removed:
        await db.commit()
        logger.info("removed %s duplicate tender library files", removed)


def _legacy_migrate_marker() -> Path:
    return slots_root() / ".kb_migrated"


async def _migrate_legacy_files(db: AsyncSession, base: KnowledgeBase) -> None:
    root = slots_root()
    marker = _legacy_migrate_marker()
    if marker.is_file():
        return
    docs = await _list_docs(db, base.id)
    # 知识库里已经有扫描件：旧 slots 只是遗留拷贝，再迁会把用户刚删的图灌回来
    if any(_doc_file_path(d) for d in docs):
        marker.write_text("1", encoding="utf-8")
        return
    if not root.is_dir():
        marker.write_text("1", encoding="utf-8")
        return
    parents = {key_from_tags(d.tags): d for d in docs if _is_material_parent(d)}
    children_by_parent = _group_children(docs)
    copied = 0
    for folder in root.iterdir():
        if not folder.is_dir():
            continue
        key = folder.name
        parent = parents.get(key)
        if parent is None:
            continue
        hashes = _linked_hashes(parent, children_by_parent.get(parent.public_id, []))
        for src in list_slot_files(key):
            if _already_has_file(hashes, src):
                src.unlink(missing_ok=True)
                continue
            child = _copy_file_as_child(base, parent, src)
            db.add(child)
            copied += 1
            if child.content_hash:
                hashes.add(str(child.content_hash))
            src.unlink(missing_ok=True)
    if copied:
        await db.commit()
        logger.info("migrated %s legacy tender slot files into knowledge", copied)
        for doc in await _list_docs(db, base.id):
            if (doc.kind or "") == "doc" and (doc.status or "") == "parsing" and int(doc.chunk_count or 0) == 0:
                if not _doc_file_path(doc):
                    continue
                try:
                    await _enqueue_library_doc(db, base=base, doc=doc, force_reextract=True)
                except Exception:
                    logger.exception("legacy library ingest enqueue failed id=%s", doc.public_id)
    marker.write_text("1", encoding="utf-8")


def _group_children(docs: list[KnowledgeDocument]) -> dict[str, list[KnowledgeDocument]]:
    out: dict[str, list[KnowledgeDocument]] = {}
    for doc in docs:
        pid = (doc.parent_id or "").strip()
        if not pid:
            continue
        out.setdefault(pid, []).append(doc)
    return out


def _library_file_tags(parent: KnowledgeDocument | None = None) -> list[str]:
    tags = [TAG_MATERIAL]
    if parent is not None:
        key = key_from_tags(parent.tags)
        if key:
            tags.append(slot_tag(key))
    return tags


def _purge_doc_vectors(doc: KnowledgeDocument) -> None:
    try:
        get_qdrant_store().delete_by_doc_id(doc.public_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("delete library vectors failed id=%s: %s", doc.public_id, exc)


def _unlink_legacy_slot_copies(key: str, doc: KnowledgeDocument) -> None:
    """知识库删了扫描件后，把旧 slots 目录里的同内容拷贝一并去掉，避免下次 ensure 再迁回来。"""
    safe = (key or "").strip()
    if not safe or not _KEY_RE.match(safe):
        return
    digest = str(doc.content_hash or "").strip()
    path = _doc_file_path(doc)
    if not digest and path is not None:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    stem = (doc.name or "").strip()
    fname = path.name if path is not None else ""
    for item in list_slot_files(safe):
        same = False
        if digest:
            try:
                same = hashlib.sha256(item.read_bytes()).hexdigest() == digest
            except OSError:
                same = False
        if not same and stem and item.stem == stem:
            same = True
        if not same and fname and item.name == fname:
            same = True
        if same:
            item.unlink(missing_ok=True)


def _copy_file_as_child(
    base: KnowledgeBase,
    parent: KnowledgeDocument,
    src: Path,
) -> KnowledgeDocument:
    public_id = short_id(12)
    ext = src.suffix.lower().lstrip(".") or "bin"
    rel_key = f"knowledge/{base.public_id}/{public_id}.{ext}"
    dest = resolve_storage_path(rel_key)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    digest = hashlib.sha256(src.read_bytes()).hexdigest()
    return KnowledgeDocument(
        public_id=public_id,
        base_id=base.id,
        name=src.stem[:255] or src.name,
        source="file",
        kind="doc",
        parent_id=parent.public_id,
        file_type=ext,
        size=src.stat().st_size,
        storage_path=rel_key,
        file_key=rel_key,
        summary="正在解析…",
        content_hash=digest,
        tags=_library_file_tags(parent),
        status="parsing",
        review_status="approved",
    )


async def _enqueue_library_doc(
    db: AsyncSession,
    *,
    base: KnowledgeBase,
    doc: KnowledgeDocument,
    force_reextract: bool = False,
) -> None:
    """把资料库扫描件送入 OCR + 向量化队列。"""
    await require_embedding_ready(db)
    tags = [str(t) for t in (doc.tags or []) if str(t).strip() and str(t).strip() != "图纸附件"]
    if TAG_MATERIAL not in tags:
        tags.insert(0, TAG_MATERIAL)
    doc.tags = tags
    doc.kind = "doc"
    if not (doc.summary or "").startswith("PERFJSON:"):
        doc.summary = "正在解析…"
    doc.status = "parsing"
    doc.review_status = "approved"
    doc.error_msg = None
    doc.chunk_count = 0
    await db.flush()
    await enqueue_ingest_task(db, base=base, doc=doc, force_reextract=force_reextract)


async def enqueue_library_rag_backfill(
    db: AsyncSession,
    *,
    force: bool = False,
) -> dict[str, int]:
    """将历史「未向量化」扫描件转为 doc 并排队 OCR 入库。"""
    base = await ensure_library_base(db)
    docs = await _list_docs(db, base.id)
    from db.models.knowledge import KnowledgeIngestTask

    candidates: list[KnowledgeDocument] = []
    for doc in docs:
        path = _doc_file_path(doc)
        if path is None:
            continue
        # 仅文件夹占位的资料项父节点跳过
        if _is_material_parent(doc) and not (doc.file_key or doc.storage_path):
            continue
        if force:
            candidates.append(doc)
            continue
        if (doc.kind or "") == "drawing":
            candidates.append(doc)
            continue
        if (doc.status or "") == "failed":
            candidates.append(doc)
            continue
        if (doc.status or "") == "ready" and int(doc.chunk_count or 0) == 0:
            candidates.append(doc)

    if not candidates:
        return {"queued": 0, "skipped": 0}

    busy_result = await db.execute(
        select(KnowledgeIngestTask.document_id).where(
            KnowledgeIngestTask.document_id.in_([d.id for d in candidates if d.id is not None]),
            KnowledgeIngestTask.status.in_(("queued", "running")),
        )
    )
    busy = {int(x) for x in busy_result.scalars().all() if x is not None}

    queued = 0
    skipped = 0
    for doc in candidates:
        if doc.id is not None and int(doc.id) in busy:
            skipped += 1
            continue
        if force or (doc.kind or "") == "drawing" or (doc.status or "") == "failed":
            _purge_doc_vectors(doc)
            force_reextract = True
        else:
            force_reextract = False
        try:
            await _enqueue_library_doc(db, base=base, doc=doc, force_reextract=force_reextract)
            queued += 1
        except Exception:
            logger.exception("library rag enqueue failed id=%s", doc.public_id)
            skipped += 1
    return {"queued": queued, "skipped": skipped}


def _iter_file_docs(
    parent: KnowledgeDocument,
    children: list[KnowledgeDocument],
) -> list[tuple[KnowledgeDocument, Path]]:
    out: list[tuple[KnowledgeDocument, Path]] = []
    own = _doc_file_path(parent)
    if own:
        out.append((parent, own))
    for child in sorted(children, key=lambda d: (d.name or "", d.public_id)):
        path = _doc_file_path(child)
        if path:
            out.append((child, path))
    return out


def _files_of(parent: KnowledgeDocument, children: list[KnowledgeDocument]) -> list[Path]:
    return [path for _, path in _iter_file_docs(parent, children)]


def _file_entry(doc: KnowledgeDocument, path: Path) -> dict[str, object]:
    ext = (doc.file_type or path.suffix.lstrip(".")).lower()
    title = (doc.name or path.stem or path.name).strip() or path.name
    line = load_perf_meta(doc.summary)
    if line and line.projectName:
        title = line.projectName
    payload: dict[str, object] = {
        "id": doc.public_id,
        "fileId": doc.public_id,
        "name": title,
        "fileName": path.name,
        "sizeBytes": path.stat().st_size,
        "fileType": ext,
        "kind": library_file_kind(ext),
    }
    if line is not None:
        payload["performance"] = line.model_dump(mode="json")
    return payload


def item_to_slot(
    parent: KnowledgeDocument,
    children: list[KnowledgeDocument],
) -> dict[str, object]:
    key = key_from_tags(parent.tags) or f"m{parent.public_id}"
    files = [_file_entry(doc, path) for doc, path in _iter_file_docs(parent, children)]
    return {
        "key": key,
        "docId": parent.public_id,
        "title": parent.name or key,
        "hint": (parent.summary or "").strip(),
        "fileCount": len(files),
        "files": files,
        "pinned": key in {s.key for s in DEFAULT_SLOTS},
    }


async def open_library_file(
    db: AsyncSession,
    doc_public_id: str,
    *,
    thumb: bool = False,
) -> tuple[bytes, str, str]:
    """读取资料库扫描件。thumb=True 返回缩小 JPEG，便于列表缩略图。"""
    pid = (doc_public_id or "").strip()
    if not pid:
        raise AppError(ErrorCode.VALIDATION, "无效的文件 id", status_code=422)
    try:
        base = await get_base_by_public_id(db, TENDER_LIB_PUBLIC_ID)
    except AppError as exc:
        raise AppError(ErrorCode.NOT_FOUND, "资料项不存在", status_code=404) from exc
    result = await db.execute(
        select(KnowledgeDocument).where(
            KnowledgeDocument.public_id == pid,
            KnowledgeDocument.base_id == base.id,
        )
    )
    doc = result.scalar_one_or_none()
    path = _doc_file_path(doc) if doc is not None else None
    if doc is None or path is None:
        raise AppError(ErrorCode.NOT_FOUND, "扫描件不存在", status_code=404)
    ext = (doc.file_type or path.suffix.lstrip(".")).lower()
    title = (doc.name or path.stem or pid).strip() or pid
    if thumb:
        try:
            jpeg = preview_jpeg_from_file(path, ext=ext, max_px=360)
        except Exception as exc:  # noqa: BLE001
            logger.warning("library thumbnail failed id=%s: %s", pid, exc)
            if library_file_kind(ext) == "image":
                return path.read_bytes(), path.name, _FILE_MIME.get(ext, "application/octet-stream")
            raise AppError(ErrorCode.BAD_REQUEST, "无法预览该文件", status_code=422) from exc
        return jpeg, f"{title}.jpg", "image/jpeg"
    return path.read_bytes(), path.name, _FILE_MIME.get(ext, "application/octet-stream")


async def list_library_items(db: AsyncSession) -> list[dict[str, object]]:
    base = await ensure_library_base(db)
    docs = await _list_docs(db, base.id)
    children = _group_children(docs)
    parents = [d for d in docs if _is_material_parent(d)]
    parents.sort(key=lambda d: (d.id or 0))
    return [item_to_slot(p, children.get(p.public_id, [])) for p in parents]


async def catalog_placeholders(db: AsyncSession) -> list[PlaceholderItem]:
    items = await list_library_items(db)
    return [
        PlaceholderItem(
            key=str(it.get("key") or ""),
            title=str(it.get("title") or ""),
            hint=str(it.get("hint") or ""),
        )
        for it in items
        if str(it.get("key") or "")
        and str(it.get("title") or "")
        and str(it.get("key") or "") != TECH_DRAWING_KEY
    ]


async def _attach_perf_extract(
    db: AsyncSession,
    doc: KnowledgeDocument,
    path: Path,
) -> PerformanceLine | None:
    line = await extract_performance_from_path(
        db, path, filename=doc.name or path.name
    )
    if line is None:
        return None
    doc.summary = dump_perf_meta(line)
    if is_weak_title(doc.name or "") and line.projectName:
        doc.name = line.projectName[:255]
    return line


async def performance_from_library(
    db: AsyncSession,
    *,
    extract_missing: bool = False,
) -> list[PerformanceLine]:
    """从资料库合同/发票已缓存的 OCR 摘要抽出业绩行。

    extract_missing=False（默认）：生成/识别时只用已缓存摘要，避免当场 OCR+LLM 拖慢。
    extract_missing=True：上传后补抽等场景可当场抽取未缓存文件。
    """
    try:
        parent, children = await _get_parent(db, "perf")
    except AppError:
        return []
    lines: list[PerformanceLine] = []
    dirty = False
    skipped = 0
    for doc, path in _iter_file_docs(parent, children):
        line = load_perf_meta(doc.summary)
        if line is None:
            if not extract_missing:
                skipped += 1
                continue
            line = await _attach_perf_extract(db, doc, path)
            if line is not None:
                dirty = True
        if line is not None and not is_weak_title(line.projectName):
            lines.append(line)
    if dirty:
        await db.commit()
    if skipped:
        logger.info(
            "performance_from_library skipped %s uncached file(s) (extract_missing=False)",
            skipped,
        )
    return rank_performance_lines(lines)[:8]


async def filter_performance_attachments(
    db: AsyncSession,
    paths: list[Path],
    selected: list,
) -> list[Path]:
    """附件只跟本标勾选的合同走，资料库其余原件不动。"""
    from api.services.tenders.performance import line_name_key

    names = {line_name_key(getattr(item, "projectName", "") or "") for item in selected or []}
    names.discard("")
    if not names:
        return []
    try:
        parent, children = await _get_parent(db, "perf")
    except AppError:
        return list(paths)
    by_resolved: dict[str, KnowledgeDocument] = {}
    for doc, path in _iter_file_docs(parent, children):
        try:
            by_resolved[str(path.resolve())] = doc
        except OSError:
            by_resolved[str(path)] = doc
    kept: list[Path] = []
    for path in paths:
        try:
            key = str(path.resolve())
        except OSError:
            key = str(path)
        doc = by_resolved.get(key)
        if doc is None:
            continue
        line = load_perf_meta(doc.summary)
        if line is None:
            continue
        if line_name_key(line.projectName) in names:
            kept.append(path)
    return kept


async def group_performance_attachments(
    db: AsyncSession,
    paths: list[Path],
    selected: list,
) -> dict[str, list[Path]]:
    """按勾选业绩把合同扫描件分组，供文字表后插首页/金额页/签字页。"""
    from api.services.tenders.performance import line_name_key

    names = [line_name_key(getattr(item, "projectName", "") or "") for item in selected or []]
    names = [n for n in names if n]
    grouped: dict[str, list[Path]] = {n: [] for n in names}
    if not names:
        return grouped
    try:
        parent, children = await _get_parent(db, "perf")
    except AppError:
        if paths and names:
            grouped[names[0]] = list(paths)
        return grouped
    by_resolved: dict[str, KnowledgeDocument] = {}
    for doc, path in _iter_file_docs(parent, children):
        try:
            by_resolved[str(path.resolve())] = doc
        except OSError:
            by_resolved[str(path)] = doc
    for path in paths:
        try:
            key = str(path.resolve())
        except OSError:
            key = str(path)
        doc = by_resolved.get(key)
        if doc is None:
            continue
        line = load_perf_meta(doc.summary)
        if line is None:
            continue
        nk = line_name_key(line.projectName)
        if nk in grouped:
            grouped[nk].append(path)
    return grouped


async def count_uncached_performance_files(db: AsyncSession) -> int:
    """尚未抽出业绩摘要的合同/发票数量（生成时可提示，不阻塞）。"""
    try:
        parent, children = await _get_parent(db, "perf")
    except AppError:
        return 0
    n = 0
    for doc, _path in _iter_file_docs(parent, children):
        if load_perf_meta(doc.summary) is None:
            n += 1
    return n


async def persist_parsed_materials(
    db: AsyncSession,
    extras: list[PlaceholderItem],
    *,
    created_by: int | None = None,
) -> list[PlaceholderItem]:
    """邀请书多出来的资料项写入资料库空项，便于当场上传。不复制其他资料项或历史项目的扫描件。"""
    existing = {str(it.get("key") or "") for it in await list_library_items(db)}
    out: list[PlaceholderItem] = []
    for item in extras:
        key = (item.key or "").strip()
        if key and key in existing:
            out.append(item)
            continue
        safe_key = key if key and _KEY_RE.match(key) else None
        created = await create_library_item(
            db,
            title=item.title,
            hint=item.hint or "",
            key=safe_key,
            created_by=created_by,
        )
        next_key = str(created.get("key") or "")
        if next_key:
            existing.add(next_key)
        out.append(
            PlaceholderItem(
                key=next_key or key,
                title=item.title,
                hint=item.hint or "",
            )
        )
    return out


async def library_payload_kb(
    db: AsyncSession,
    *,
    created_by: int | None = None,
) -> dict[str, object]:
    base = await ensure_library_base(db, created_by=created_by)
    try:
        await enqueue_library_rag_backfill(db, force=False)
    except Exception:
        logger.exception("library rag backfill skipped")
    slots = await list_library_items(db)
    slots = [s for s in slots if str(s.get("key") or "") != TECH_DRAWING_KEY]
    filled = sum(1 for s in slots if int(s.get("fileCount") or 0) > 0)
    return {
        "slots": slots,
        "filledCount": filled,
        "totalCount": len(slots),
        "baseId": base.public_id,
        "hint": (
            "资料存放在知识库「投标资料库」中。上传后会 OCR 入库，"
            "可在「AI 智能问答」中勾选本库检索；生成投标文件时按名称自动引用。"
        ),
    }


async def list_slots_status_kb(
    db: AsyncSession,
    extra: list[PlaceholderItem] | None = None,
    *,
    include_keys: list[str] | None = None,
    invitation_id: str = "",
) -> list[dict[str, object]]:
    catalog = await catalog_placeholders(db)
    merged = collect_slots(extra, catalog=catalog, include_keys=include_keys)
    by_key = {str(it.get("key") or ""): it for it in await list_library_items(db)}
    out: list[dict[str, object]] = []
    for item in merged:
        current = by_key.get(item.key)
        if current:
            current = dict(current)
            current["title"] = item.title or current.get("title")
            current["hint"] = item.hint or current.get("hint")
            out.append(current)
        elif item.key == TECH_DRAWING_KEY:
            row = drawing_slot_status(invitation_id)
            row["title"] = item.title or row.get("title")
            row["hint"] = item.hint or row.get("hint")
            out.append(row)
        else:
            out.append(
                {
                    "key": item.key,
                    "docId": "",
                    "title": item.title,
                    "hint": item.hint or "",
                    "fileCount": 0,
                    "files": [],
                    "pinned": False,
                }
            )
    return out


async def resolve_attachments(
    db: AsyncSession,
    slots: list[PlaceholderItem],
) -> dict[str, list[Path]]:
    try:
        base = await get_base_by_public_id(db, TENDER_LIB_PUBLIC_ID)
        docs = await _list_docs(db, base.id)
    except AppError:
        docs = []
    parents = {key_from_tags(d.tags): d for d in docs if _is_material_parent(d)}
    children = _group_children(docs)
    out: dict[str, list[Path]] = {}
    for item in slots:
        key = (item.key or "").strip()
        if not key or key == TECH_DRAWING_KEY:
            continue
        parent = parents.get(key)
        paths: list[Path] = []
        if parent is not None:
            paths = _files_of(parent, children.get(parent.public_id, []))
        # 只允许同一 key 的扫描件；禁止拿其他资料项或历史项目文件顶替。
        if not paths and _KEY_RE.match(key):
            paths = list_slot_files(key)
        if paths:
            out[key] = paths
    return out


async def create_library_item(
    db: AsyncSession,
    *,
    title: str,
    hint: str = "",
    key: str | None = None,
    created_by: int | None = None,
) -> dict[str, object]:
    name = (title or "").strip()
    if not name:
        raise AppError(ErrorCode.VALIDATION, "请填写资料名称", status_code=422)
    base = await ensure_library_base(db, created_by=created_by)
    items = await list_library_items(db)
    used = {str(it.get("key") or "") for it in items}
    public_id = short_id(12)
    raw_key = (key or "").strip()
    if raw_key:
        if not _KEY_RE.match(raw_key):
            raise AppError(ErrorCode.VALIDATION, "资料项 key 仅支持字母数字和 _-", status_code=422)
        next_key = raw_key
    else:
        next_key = slug_key(name, public_id)
    if next_key in used:
        next_key = f"m{public_id}"
    if next_key in used:
        raise AppError(ErrorCode.CONFLICT, "资料项已存在", status_code=409)
    parent = _new_parent(
        base_id=base.id,
        public_id=public_id,
        key=next_key,
        title=name,
        hint=(hint or "").strip(),
    )
    parent.created_by = created_by
    db.add(parent)
    await db.commit()
    await db.refresh(parent)
    return item_to_slot(parent, [])


async def update_library_item(
    db: AsyncSession,
    key: str,
    *,
    title: str | None = None,
    hint: str | None = None,
) -> dict[str, object]:
    parent, children = await _get_parent(db, key)
    if title is not None:
        name = title.strip()
        if not name:
            raise AppError(ErrorCode.VALIDATION, "请填写资料名称", status_code=422)
        parent.name = name[:255]
    if hint is not None:
        note = hint.strip() or parent.name
        if len(note) < 4:
            note = f"{note} 扫描件"
        parent.summary = note[:200]
    await db.commit()
    await db.refresh(parent)
    return item_to_slot(parent, children)


async def delete_library_item(db: AsyncSession, key: str) -> dict[str, object]:
    parent, _children = await _get_parent(db, key)
    base = await ensure_library_base(db)
    deleted = await delete_document_record(
        db, base_public_id=base.public_id, doc_public_id=parent.public_id
    )
    clear_slot(key)
    await db.commit()
    return {"key": key, "deleted": bool(deleted)}


async def _get_parent(
    db: AsyncSession, key: str
) -> tuple[KnowledgeDocument, list[KnowledgeDocument]]:
    safe = (key or "").strip()
    if not safe or not _KEY_RE.match(safe):
        raise AppError(ErrorCode.VALIDATION, "无效的附件项 key", status_code=422)
    base = await ensure_library_base(db)
    docs = await _list_docs(db, base.id)
    parent = next((d for d in docs if _is_material_parent(d) and key_from_tags(d.tags) == safe), None)
    if parent is None:
        raise AppError(ErrorCode.NOT_FOUND, "资料项不存在", status_code=404)
    children = _group_children(docs).get(parent.public_id, [])
    return parent, children


async def save_library_file(
    db: AsyncSession,
    key: str,
    *,
    filename: str,
    data: bytes,
    replace: bool = False,
) -> dict[str, object]:
    if not data:
        raise AppError(ErrorCode.VALIDATION, "上传文件为空", status_code=422)
    if len(data) > _MAX_BYTES:
        raise AppError(ErrorCode.VALIDATION, "单个文件不能超过 40MB", status_code=422)
    name = Path((filename or "upload.bin").replace("\\", "/").split("/")[-1]).name
    suffix = Path(name).suffix.lower()
    if suffix not in _ALLOWED_EXT:
        raise AppError(ErrorCode.VALIDATION, "仅支持 PDF / PNG / JPG / WEBP / GIF / BMP", status_code=422)

    try:
        parent, children = await _get_parent(db, key)
    except AppError as exc:
        if exc.status_code != 404:
            raise
        await create_library_item(db, title=key, hint="", key=key)
        parent, children = await _get_parent(db, key)
    base = await ensure_library_base(db)
    if replace:
        for child in children:
            _purge_doc_vectors(child)
            unlink_stored_file(child.file_key or child.storage_path)
            await db.delete(child)
        if parent.file_key or parent.storage_path:
            _purge_doc_vectors(parent)
            unlink_stored_file(parent.file_key or parent.storage_path)
            parent.file_key = None
            parent.storage_path = None
            parent.size = None
        children = []

    if len(_files_of(parent, children)) >= _MAX_FILES_PER_SLOT:
        raise AppError(ErrorCode.VALIDATION, f"每项最多 {_MAX_FILES_PER_SLOT} 个文件", status_code=422)

    await require_embedding_ready(db)
    public_id = short_id(12)
    ext = suffix.lstrip(".")
    rel_key = f"knowledge/{base.public_id}/{public_id}.{ext}"
    dest = resolve_storage_path(rel_key)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    child = KnowledgeDocument(
        public_id=public_id,
        base_id=base.id,
        name=Path(name).stem[:255] or name,
        source="file",
        kind="doc",
        parent_id=parent.public_id,
        file_type=ext,
        size=len(data),
        storage_path=rel_key,
        file_key=rel_key,
        summary="正在解析…",
        content_hash=hashlib.sha256(data).hexdigest(),
        tags=_library_file_tags(parent),
        status="parsing",
        review_status="approved",
    )
    db.add(child)
    await db.commit()
    await db.refresh(child)
    if key == "perf":
        try:
            await _attach_perf_extract(db, child, dest)
            await db.commit()
            await db.refresh(child)
        except Exception:
            logger.exception("类似业绩抽取失败 file=%s", name)
            await db.rollback()
            await db.refresh(child)
    try:
        await _enqueue_library_doc(db, base=base, doc=child, force_reextract=True)
    except Exception:
        logger.exception("library OCR enqueue failed file=%s", name)
        child.status = "failed"
        child.error_msg = "无法排队 OCR 入库，请检查 Embedding 配置"
        await db.commit()
    parent, children = await _get_parent(db, key)
    payload = item_to_slot(parent, children)
    payload["fileName"] = dest.name
    payload["sizeBytes"] = dest.stat().st_size
    return payload


async def delete_library_file(db: AsyncSession, doc_public_id: str) -> dict[str, object]:
    """删除资料项下的一份扫描件。父项本身只摘掉挂在文件夹上的文件，不删资料项。"""
    pid = (doc_public_id or "").strip()
    if not pid:
        raise AppError(ErrorCode.VALIDATION, "无效的文件 id", status_code=422)
    base = await ensure_library_base(db)
    result = await db.execute(
        select(KnowledgeDocument).where(
            KnowledgeDocument.public_id == pid,
            KnowledgeDocument.base_id == base.id,
        )
    )
    doc = result.scalar_one_or_none()
    if doc is None:
        raise AppError(ErrorCode.NOT_FOUND, "扫描件不存在", status_code=404)

    if _is_material_parent(doc):
        if not (doc.file_key or doc.storage_path):
            raise AppError(ErrorCode.VALIDATION, "该项没有可单独删除的文件", status_code=422)
        _unlink_legacy_slot_copies(key_from_tags(doc.tags), doc)
        _purge_doc_vectors(doc)
        unlink_stored_file(doc.file_key or doc.storage_path)
        doc.file_key = None
        doc.storage_path = None
        doc.size = None
        await db.commit()
        docs = await _list_docs(db, base.id)
        children = _group_children(docs).get(doc.public_id, [])
        payload = item_to_slot(doc, children)
        if int(payload.get("fileCount") or 0) == 0:
            clear_slot(key_from_tags(doc.tags))
        return payload

    parent_pid = (doc.parent_id or "").strip()
    if not parent_pid:
        raise AppError(ErrorCode.VALIDATION, "不能删除资料项本身，请用「删除项」", status_code=422)
    parent_row = await db.execute(
        select(KnowledgeDocument).where(
            KnowledgeDocument.public_id == parent_pid,
            KnowledgeDocument.base_id == base.id,
        )
    )
    parent = parent_row.scalar_one_or_none()
    slot_key = key_from_tags(parent.tags) if parent is not None else ""
    _unlink_legacy_slot_copies(slot_key, doc)
    deleted = await delete_document_record(
        db, base_public_id=base.public_id, doc_public_id=doc.public_id
    )
    await db.commit()
    if deleted is None:
        raise AppError(ErrorCode.NOT_FOUND, "扫描件不存在", status_code=404)
    docs = await _list_docs(db, base.id)
    parent = next((d for d in docs if d.public_id == parent_pid), None)
    if parent is None:
        raise AppError(ErrorCode.NOT_FOUND, "资料项不存在", status_code=404)
    children = _group_children(docs).get(parent.public_id, [])
    payload = item_to_slot(parent, children)
    if int(payload.get("fileCount") or 0) == 0:
        clear_slot(slot_key)
    return payload


async def clear_library_files(db: AsyncSession, key: str) -> dict[str, object]:
    parent, children = await _get_parent(db, key)
    removed = 0
    for child in children:
        _purge_doc_vectors(child)
        unlink_stored_file(child.file_key or child.storage_path)
        await db.delete(child)
        removed += 1
    if parent.file_key or parent.storage_path:
        _purge_doc_vectors(parent)
        unlink_stored_file(parent.file_key or parent.storage_path)
        parent.file_key = None
        parent.storage_path = None
        parent.size = None
        removed += 1
    removed += clear_slot(key)
    await db.commit()
    parent, children = await _get_parent(db, key)
    payload = item_to_slot(parent, children)
    payload["removed"] = removed
    return payload


def catalog_prompt_lines(items: list[dict[str, object]]) -> str:
    if not items:
        return "（资料库为空）"
    lines = []
    for it in items:
        filled = "已有扫描件" if int(it.get("fileCount") or 0) > 0 else "待上传"
        lines.append(
            f"- key={it.get('key')} title={it.get('title')} status={filled} hint={it.get('hint') or ''}"
        )
    return "\n".join(lines)
