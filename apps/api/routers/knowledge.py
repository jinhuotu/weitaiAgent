"""知识库 API：库 CRUD、文档入库（文本/文件/URL）、检索、预览/下载。

向量正文在 Qdrant；MySQL 只存文档卡片。入库前需在模型管理启用 Embedding。
权限（查看 / 使用 / 维护）：
  GET/PUT /knowledge/bases/{id}/acl
"""

from __future__ import annotations
import json
import re
import httpx
from fastapi import APIRouter, File, Form, Query, UploadFile
from urllib.parse import quote

from fastapi.responses import FileResponse, Response

from api.deps import AdminUser, CurrentUser, DbSession
from api.services.menus import user_is_admin
from api.schemas.knowledge import (
    AttachDocumentRequest,
    CreateBaseRequest,
    QdrantApplyRequest,
    ReplaceAclRequest,
    ReviewDocumentRequest,
    SearchRequest,
    TextDocumentRequest,
    UpdateBaseRequest,
    UrlDocumentRequest,
)
from api.services.knowledge import access as kb_access
from api.services.knowledge import bases as bases_svc
from api.services.knowledge.ingest import (
    attach_document,
    delete_document,
    download_document,
    ensure_upload_allowed,
    find_duplicate_documents,
    get_document_preview,
    ingest_text,
    ingest_upload,
    list_documents,
    reparse_document,
    resolve_document_file,
    search_chunks,
    unlink_base_storage,
)
from api.services.knowledge.queue import cancel_ingest_task, list_ingest_tasks
from api.services.knowledge.review import list_document_reviews, reindex_approved_documents, review_document
from api.services.knowledge.qdrant_store import get_qdrant_store
from common.errors import AppError, ErrorCode
from common.response import ok

router = APIRouter(prefix="/knowledge", tags=["knowledge"])
_TAG_RE = re.compile(r"<[^>]+>")


def _html_to_text(raw: str) -> str:
    text = re.sub(r"(?is)<script.*?>.*?</script>", " ", raw)
    text = re.sub(r"(?is)<style.*?>.*?</style>", " ", text)
    text = _TAG_RE.sub(" ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _parse_tags(raw: str | None, *, default: list[str]) -> list[str]:
    if raw is None or not str(raw).strip():
        return default
    text = str(raw).strip()
    if text.startswith("["):
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise AppError(ErrorCode.VALIDATION, "tags 必须是 JSON 数组或逗号分隔", status_code=422) from exc
        if not isinstance(data, list):
            raise AppError(ErrorCode.VALIDATION, "tags 必须是 JSON 数组或逗号分隔", status_code=422)
        return [str(x).strip() for x in data if str(x).strip()]
    return [p.strip() for p in text.split(",") if p.strip()]


def _parse_bool_form(raw: str | None) -> bool | None:
    if raw is None or not str(raw).strip():
        return None
    text = str(raw).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return None


# ---------- knowledge bases ----------
@router.get("/bases")
async def bases_list(
    db: DbSession,
    user: CurrentUser,
    access: str = Query(default="view"),
) -> dict:
    perm: kb_access.Perm = "view"
    raw = (access or "view").strip().lower()
    if raw in {kb_access.PERM_VIEW, kb_access.PERM_USE, kb_access.PERM_MANAGE}:
        perm = raw  # type: ignore[assignment]
    items, can_create = await kb_access.list_visible_bases(db, user, access=perm)
    return ok({"items": items, "canCreate": can_create})


@router.post("/bases")
async def bases_create(body: CreateBaseRequest, db: DbSession, user: CurrentUser) -> dict:
    if not user_is_admin(user):
        raise AppError(ErrorCode.FORBIDDEN, "仅管理员可创建知识库", status_code=403)
    item = await bases_svc.create_base(
        db,
        name=body.name,
        description=body.description,
        created_by=user.id,
    )
    return ok({"item": kb_access.attach_perms(item, set(kb_access.ALL_PERMS))})


@router.get("/bases/{base_id}")
async def bases_get(base_id: str, db: DbSession, user: CurrentUser) -> dict:
    base = await kb_access.require_base(db, user, base_id, kb_access.PERM_VIEW)
    flags = await kb_access.perms_for_base(db, user, base)
    return ok({"item": kb_access.attach_perms(bases_svc.to_base_item(base), flags)})


@router.get("/bases/{base_id}/acl")
async def bases_acl_get(base_id: str, db: DbSession, user: CurrentUser) -> dict:
    return ok(await kb_access.list_acl(db, user, base_id))


@router.put("/bases/{base_id}/acl")
async def bases_acl_put(
    base_id: str, body: ReplaceAclRequest, db: DbSession, user: CurrentUser
) -> dict:
    data = await kb_access.replace_acl(
        db,
        user,
        base_id,
        [g.model_dump() for g in body.grants],
    )
    return ok(data)


@router.patch("/bases/{base_id}")
async def bases_update(
    base_id: str,
    body: UpdateBaseRequest,
    db: DbSession,
    user: CurrentUser,
) -> dict:
    await kb_access.require_base(db, user, base_id, kb_access.PERM_MANAGE)
    item = await bases_svc.update_base(
        db,
        public_id=base_id,
        name=body.name,
        description=body.description,
    )
    base = await bases_svc.get_base_by_public_id(db, base_id)
    flags = await kb_access.perms_for_base(db, user, base)
    return ok({"item": kb_access.attach_perms(item, flags)})


@router.delete("/bases/{base_id}")
async def bases_delete(base_id: str, db: DbSession, user: CurrentUser) -> dict:
    await kb_access.require_base(db, user, base_id, kb_access.PERM_MANAGE)
    base = await bases_svc.get_base_by_public_id(db, base_id)
    unlink_base_storage(base_id, list(base.documents or []))
    result = await bases_svc.delete_base(db, public_id=base_id)
    store = get_qdrant_store()
    try:
        store.delete_by_kb_id(base_id)
    except Exception:  # noqa: BLE001
        for doc_id in result.get("deletedDocIds") or []:
            store.delete_by_doc_id(doc_id)
    return ok(result)


# ---------- documents (scoped by baseId) ----------
@router.get("/documents")
async def documents_list(
    db: DbSession,
    user: CurrentUser,
    baseId: str = Query(..., min_length=1, max_length=32),
    reviewStatus: str | None = Query(default=None),
) -> dict:
    await kb_access.require_base(db, user, baseId, kb_access.PERM_VIEW)
    items = await list_documents(db, base_public_id=baseId, review_status=reviewStatus)
    return {"code": 0, "msg": "ok", "data": {"items": items}, "items": items}


@router.get("/ingest-tasks")
async def ingest_tasks_list(
    db: DbSession,
    user: CurrentUser,
    baseId: str = Query(..., min_length=1, max_length=32),
) -> dict:
    await kb_access.require_base(db, user, baseId, kb_access.PERM_VIEW)
    items = await list_ingest_tasks(db, base_public_id=baseId)
    return ok({"items": items})


@router.post("/ingest-tasks/{task_id}/cancel")
async def ingest_tasks_cancel(
    task_id: str,
    db: DbSession,
    user: CurrentUser,
    baseId: str = Query(..., min_length=1, max_length=32),
) -> dict:
    await kb_access.require_base(db, user, baseId, kb_access.PERM_MANAGE)
    data = await cancel_ingest_task(db, base_public_id=baseId, task_public_id=task_id)
    return ok(data)


@router.get("/qdrant/settings")
async def qdrant_settings_get(admin: AdminUser) -> dict:
    _ = admin
    store = get_qdrant_store()
    return ok({"item": store.collection_info()})


@router.post("/qdrant/settings/apply")
async def qdrant_settings_apply(
    body: QdrantApplyRequest, db: DbSession, admin: AdminUser
) -> dict:
    _ = admin
    if not body.confirm:
        raise AppError(ErrorCode.VALIDATION, "请确认重建向量集合（confirm=true）", status_code=422)
    store = get_qdrant_store()
    from api.services.knowledge.embeddings import get_embedding_client

    embedder = await get_embedding_client(db)
    dim = int(embedder.dim or 1536)
    store.recreate_collection(dim)
    stats = await reindex_approved_documents(db)
    info = store.collection_info()
    return ok({"item": info, "reindex": stats})


@router.get("/documents/check-duplicate")
async def documents_check_duplicate(
    db: DbSession,
    user: CurrentUser,
    baseId: str = Query(..., min_length=1, max_length=32),
    name: str = Query(..., min_length=1, max_length=255),
    url: str | None = Query(default=None, max_length=1024),
) -> dict:
    await kb_access.require_base(db, user, baseId, kb_access.PERM_MANAGE)
    dupes = await find_duplicate_documents(
        db, base_public_id=baseId, name=name, url=url
    )
    return ok({"duplicates": dupes, "exists": bool(dupes)})


@router.post("/documents/upload")
async def documents_upload(
    db: DbSession,
    user: CurrentUser,
    file: UploadFile = File(..., description="原文件：pdf / docx / xlsx / pptx / 图片 / txt / md / csv"),
    baseId: str = Form(..., min_length=1, max_length=32),
    name: str | None = Form(None, max_length=255),
    tags: str | None = Form(None, description="JSON 数组或逗号分隔"),
    parentId: str | None = Form(None, max_length=32),
    asAttachment: str | None = Form(None, description="1/true=图纸附件，不 OCR 进向量"),
    force: str | None = Form(None, description="1/true=覆盖同名资料"),
) -> dict:
    await kb_access.require_base(db, user, baseId, kb_access.PERM_MANAGE)
    filename = file.filename or "upload.bin"
    display_name = (name or "").strip() or filename
    force_bool = _parse_bool_form(force) or False
    replaced = await ensure_upload_allowed(
        db,
        base_public_id=baseId,
        name=display_name,
        force=force_bool,
    )
    item = await ingest_upload(
        db,
        base_public_id=baseId,
        upload=file,
        name=name,
        tags=_parse_tags(tags, default=["手动上传"]),
        uploader=user.display_name or user.username,
        created_by=user.id,
        parent_id=parentId,
        as_attachment=_parse_bool_form(asAttachment),
    )
    items = await list_documents(db, base_public_id=baseId)
    return {
        "code": 0,
        "msg": "ok",
        "data": {"item": item, "items": items, "replaced": replaced},
        "item": item,
        "items": items,
        "replaced": replaced,
    }


@router.post("/documents/from-text")
async def documents_from_text(
    body: TextDocumentRequest,
    db: DbSession,
    user: CurrentUser,
) -> dict:
    await kb_access.require_base(db, user, body.baseId, kb_access.PERM_MANAGE)
    replaced = await ensure_upload_allowed(
        db,
        base_public_id=body.baseId,
        name=body.title,
        force=body.force,
    )
    item = await ingest_text(
        db,
        base_public_id=body.baseId,
        name=body.title,
        content=body.content,
        source="text",
        file_type="txt",
        tags=body.tags or [],
        uploader=body.uploader or user.display_name or user.username,
        created_by=user.id,
    )
    items = await list_documents(db, base_public_id=body.baseId)
    return {
        "code": 0,
        "msg": "ok",
        "data": {"item": item, "items": items, "replaced": replaced},
        "item": item,
        "items": items,
        "replaced": replaced,
    }


@router.post("/documents/from-url")
async def documents_from_url(
    body: UrlDocumentRequest,
    db: DbSession,
    user: CurrentUser,
) -> dict:
    await kb_access.require_base(db, user, body.baseId, kb_access.PERM_MANAGE)
    url = body.url.strip()
    title = (body.title or url).strip()
    replaced = await ensure_upload_allowed(
        db,
        base_public_id=body.baseId,
        name=title,
        url=url,
        force=body.force,
    )
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "weitai-agent-kb/0.1"})
            resp.raise_for_status()
            raw = resp.text
    except Exception as exc:  # noqa: BLE001
        raise AppError(ErrorCode.BAD_REQUEST, f"fetch url failed: {exc}", status_code=400) from exc
    content = _html_to_text(raw)
    if len(content) < 4:
        raise AppError(ErrorCode.VALIDATION, "url content too short after parse", status_code=422)
    item = await ingest_text(
        db,
        base_public_id=body.baseId,
        name=title,
        content=content[:200_000],
        source="url",
        file_type="html",
        url=url,
        size=len(content.encode("utf-8")),
        tags=body.tags or ["URL"],
        uploader=body.uploader or user.display_name or user.username,
        created_by=user.id,
    )
    items = await list_documents(db, base_public_id=body.baseId)
    return {
        "code": 0,
        "msg": "ok",
        "data": {"item": item, "items": items, "replaced": replaced},
        "item": item,
        "items": items,
        "replaced": replaced,
    }


@router.post("/search")
async def knowledge_search(body: SearchRequest, db: DbSession, user: CurrentUser) -> dict:
    if body.baseId:
        await kb_access.require_base(db, user, body.baseId, kb_access.PERM_USE)
        kb_ids = [body.baseId]
    else:
        kb_ids = await kb_access.usable_public_ids(db, user)
        if not kb_ids:
            return {"code": 0, "msg": "ok", "data": {"chunks": []}, "chunks": []}
    chunks = await search_chunks(
        db,
        query=body.query,
        top_k=body.topK,
        min_score=body.minScore,
        kb_ids=kb_ids,
    )
    return {"code": 0, "msg": "ok", "data": {"chunks": chunks}, "chunks": chunks}


@router.get("/documents/{public_id}/preview")
async def documents_preview(
    public_id: str,
    db: DbSession,
    user: CurrentUser,
    baseId: str = Query(..., min_length=1, max_length=32),
) -> dict:
    await kb_access.require_base(db, user, baseId, kb_access.PERM_VIEW)
    data = await get_document_preview(db, base_public_id=baseId, doc_public_id=public_id)
    return ok(data)


@router.post("/documents/{public_id}/review")
async def documents_review(
    public_id: str,
    body: ReviewDocumentRequest,
    db: DbSession,
    user: CurrentUser,
) -> dict:
    await kb_access.require_base(db, user, body.baseId, kb_access.PERM_MANAGE)
    item = await review_document(
        db,
        base_public_id=body.baseId,
        doc_public_id=public_id,
        action=body.action,
        comment=body.comment,
        actor_id=user.id,
    )
    items = await list_documents(db, base_public_id=body.baseId)
    return ok({"item": item, "items": items})


@router.get("/documents/{public_id}/reviews")
async def documents_reviews(
    public_id: str,
    db: DbSession,
    user: CurrentUser,
    baseId: str = Query(..., min_length=1, max_length=32),
) -> dict:
    await kb_access.require_base(db, user, baseId, kb_access.PERM_VIEW)
    items = await list_document_reviews(db, base_public_id=baseId, doc_public_id=public_id)
    return ok({"items": items})


@router.post("/documents/{public_id}/reparse")
async def documents_reparse(
    public_id: str,
    db: DbSession,
    user: CurrentUser,
    baseId: str = Query(..., min_length=1, max_length=32),
) -> dict:
    await kb_access.require_base(db, user, baseId, kb_access.PERM_MANAGE)
    item = await reparse_document(db, base_public_id=baseId, doc_public_id=public_id)
    items = await list_documents(db, base_public_id=baseId)
    return {
        "code": 0,
        "msg": "ok",
        "data": {"item": item, "items": items},
        "item": item,
        "items": items,
    }


@router.patch("/documents/{public_id}/attach")
async def documents_attach(
    public_id: str,
    body: AttachDocumentRequest,
    db: DbSession,
    user: CurrentUser,
) -> dict:
    await kb_access.require_base(db, user, body.baseId, kb_access.PERM_MANAGE)
    item = await attach_document(
        db,
        base_public_id=body.baseId,
        doc_public_id=public_id,
        parent_id=body.parentId,
        as_attachment=body.asAttachment,
    )
    items = await list_documents(db, base_public_id=body.baseId)
    return {
        "code": 0,
        "msg": "ok",
        "data": {"item": item, "items": items},
        "item": item,
        "items": items,
    }


@router.get("/documents/{public_id}/download")
async def documents_download(
    public_id: str,
    db: DbSession,
    user: CurrentUser,
    baseId: str = Query(..., min_length=1, max_length=32),
) -> Response:
    await kb_access.require_base(db, user, baseId, kb_access.PERM_VIEW)
    data, filename, media_type = await download_document(
        db, base_public_id=baseId, doc_public_id=public_id
    )
    disposition = f"attachment; filename*=UTF-8''{quote(filename)}"
    return Response(
        content=data,
        media_type=media_type,
        headers={
            "Content-Disposition": disposition,
            "Cache-Control": "private, max-age=60",
        },
    )


@router.get("/documents/{public_id}/file")
async def documents_file(
    public_id: str,
    db: DbSession,
    user: CurrentUser,
    baseId: str = Query(..., min_length=1, max_length=32),
) -> FileResponse:
    """原件流式播放/预览：支持 Range，供 <video src> 边下边播。"""
    await kb_access.require_base(db, user, baseId, kb_access.PERM_VIEW)
    path, filename, media_type = await resolve_document_file(
        db, base_public_id=baseId, doc_public_id=public_id
    )
    return FileResponse(
        path,
        media_type=media_type,
        filename=filename,
        content_disposition_type="inline",
        headers={
            "Accept-Ranges": "bytes",
            "Cache-Control": "private, max-age=60",
        },
    )


@router.delete("/documents/{public_id}")
async def documents_delete(
    public_id: str,
    db: DbSession,
    user: CurrentUser,
    baseId: str = Query(..., min_length=1, max_length=32),
) -> dict:
    await kb_access.require_base(db, user, baseId, kb_access.PERM_MANAGE)
    payload = await delete_document(db, base_public_id=baseId, doc_public_id=public_id)
    return {
        "code": 0,
        "msg": "ok",
        "data": payload,
        "items": payload.get("items") or [],
    }
