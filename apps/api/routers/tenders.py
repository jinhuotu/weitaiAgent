"""投标文件：邀请书抽取、填表生成 Word，下载 docx / 资质 PDF；待补附件上传。"""

from __future__ import annotations

import json
import re

from fastapi import APIRouter, File, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response

from api.deps import CurrentUser, DbSession
from api.services.tenders.extract import parse_invitation, parse_kb_ids
from api.services.tenders.generate import defaults_payload_kb, resolve_output_file
from api.services.tenders.library_kb import (
    clear_library_files,
    create_library_item,
    delete_library_file,
    delete_library_item,
    library_payload_kb,
    list_slots_status_kb,
    open_library_file,
    save_library_file,
    update_library_item,
)
from api.services.tenders.records import (
    create_record_from_generate,
    delete_record,
    get_record,
    list_records,
    regenerate_from_record,
)
from api.services.tenders.onlyoffice import (
    build_editor_config,
    handle_callback,
    onlyoffice_enabled,
    verify_download_token,
)
from api.services.tenders.schema import BidBrief, PlaceholderItem
from common.config import get_settings
from api.services.tenders.placeholders import TECH_DRAWING_KEY
from api.services.tenders.slots import sanitize_slot_key, save_drawing_file, clear_drawing_files
from pydantic import BaseModel, Field
from common.errors import AppError, ErrorCode
from common.response import ok

router = APIRouter(prefix="/tenders", tags=["tenders"])

_MIME = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pdf": "application/pdf",
}


class LibraryItemIn(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    hint: str = ""
    key: str | None = None


class LibraryItemPatch(BaseModel):
    title: str | None = Field(default=None, max_length=255)
    hint: str | None = None


@router.get("/defaults")
async def tenders_defaults(db: DbSession, user: CurrentUser):
    return ok(await defaults_payload_kb(db, created_by=int(user.id)))


@router.get("/library")
async def tenders_library(db: DbSession, user: CurrentUser):
    """投标资料库：知识库中的可维护扫描件清单。"""
    return ok(await library_payload_kb(db, created_by=int(user.id)))


@router.get("/library/files/{doc_id}")
async def tenders_library_file(
    doc_id: str,
    db: DbSession,
    user: CurrentUser,
    thumb: bool = Query(False, description="true=缩略图 JPEG"),
):
    del user
    data, filename, media_type = await open_library_file(db, doc_id, thumb=thumb)
    ascii_name = re.sub(r"[^\w.\-]+", "_", filename) or "preview.jpg"
    return Response(
        content=data,
        media_type=media_type,
        headers={
            "Cache-Control": "private, max-age=300",
            "Content-Disposition": f"inline; filename*=UTF-8''{ascii_name}",
        },
    )


@router.delete("/library/files/{doc_id}")
async def tenders_delete_library_file(doc_id: str, db: DbSession, user: CurrentUser):
    del user
    return ok(await delete_library_file(db, doc_id))


@router.post("/library/items")
async def tenders_create_library_item(body: LibraryItemIn, db: DbSession, user: CurrentUser):
    return ok(
        await create_library_item(
            db,
            title=body.title,
            hint=body.hint or "",
            key=body.key,
            created_by=int(user.id),
        )
    )


@router.patch("/library/items/{key}")
async def tenders_update_library_item(
    key: str,
    body: LibraryItemPatch,
    db: DbSession,
    user: CurrentUser,
):
    del user
    return ok(await update_library_item(db, key, title=body.title, hint=body.hint))


@router.delete("/library/items/{key}")
async def tenders_delete_library_item(key: str, db: DbSession, user: CurrentUser):
    del user
    return ok(await delete_library_item(db, key))


@router.get("/slots")
async def tenders_list_slots(
    db: DbSession,
    user: CurrentUser,
    extras: str | None = None,
):
    """待补附件槽位状态。extras 为可选 JSON 数组（邀请书多出来的项）。"""
    del user
    return ok({"slots": await list_slots_status_kb(db, _parse_extras(extras))})


@router.post("/slots/{key}")
async def tenders_upload_slot(
    key: str,
    db: DbSession,
    user: CurrentUser,
    file: UploadFile = File(..., description="扫描件：pdf / png / jpg / webp / gif / bmp"),
    replace: str | None = Form("true", description="true=替换该项全部文件；false=追加"),
):
    del user
    safe = sanitize_slot_key(key)
    raw = await file.read()
    do_replace = str(replace or "true").strip().lower() not in {"0", "false", "no"}
    if safe == TECH_DRAWING_KEY:
        return ok(
            save_drawing_file(
                filename=file.filename or "upload.bin",
                data=raw,
                replace=do_replace,
            )
        )
    payload = await save_library_file(
        db,
        safe,
        filename=file.filename or "upload.bin",
        data=raw,
        replace=do_replace,
    )
    return ok(payload)


@router.delete("/slots/{key}")
async def tenders_clear_slot(key: str, db: DbSession, user: CurrentUser):
    del user
    safe = sanitize_slot_key(key)
    if safe == TECH_DRAWING_KEY:
        return ok(clear_drawing_files())
    return ok(await clear_library_files(db, safe))


@router.post("/parse-invitation")
async def tenders_parse_invitation(
    db: DbSession,
    user: CurrentUser,
    file: UploadFile = File(..., description="投标邀请书 / 招标文件：pdf / docx / xlsx / txt / 图片"),
    quoteFile: UploadFile | None = File(None, description="可选：工程量清单 Excel/Word"),
    kbIds: str | None = Form(None, description="知识库 ID：JSON 数组或逗号分隔"),
    current: str | None = Form(None, description="当前表单 JSON，抽取结果合并到其上"),
):
    brief = _parse_current_brief(current)
    payload = await parse_invitation(
        db,
        file,
        kb_ids=parse_kb_ids(kbIds),
        current=brief,
        quote_upload=quoteFile,
        created_by=int(user.id),
    )
    brief_data = payload.get("brief") if isinstance(payload.get("brief"), dict) else {}
    extras = _coerce_placeholders(brief_data.get("extraPlaceholders"))
    include_keys = [
        str(k).strip()
        for k in (brief_data.get("includeSlotKeys") or payload.get("includeSlotKeys") or [])
        if str(k).strip()
    ]
    payload["slots"] = await list_slots_status_kb(
        db,
        extras,
        include_keys=include_keys,
    )
    payload["catalogSlots"] = await list_slots_status_kb(db)
    return ok(payload)


@router.post("/generate")
async def tenders_generate(body: BidBrief, db: DbSession, user: CurrentUser):
    payload = await create_record_from_generate(
        db,
        body,
        user_id=int(user.id),
        username=user.username or "",
    )
    return ok(payload)


@router.get("/records")
async def tenders_list_records(
    db: DbSession,
    user: CurrentUser,
    q: str | None = Query(None, description="按项目名 / 招标人 / 创建人搜索"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    del user
    return ok(await list_records(db, q=q, limit=limit, offset=offset))


@router.get("/records/{record_id}")
async def tenders_get_record(record_id: str, db: DbSession, user: CurrentUser):
    del user
    return ok(await get_record(db, record_id))


@router.delete("/records/{record_id}")
async def tenders_delete_record(record_id: str, db: DbSession, user: CurrentUser):
    del user
    return ok(await delete_record(db, record_id))


@router.post("/records/{record_id}/regenerate")
async def tenders_regenerate_record(record_id: str, db: DbSession, user: CurrentUser):
    """用该记录保存的表单和当前资料库附件再生成一份。"""
    payload = await regenerate_from_record(
        db,
        record_id,
        user_id=int(user.id),
        username=user.username or "",
    )
    return ok(payload)


@router.get("/files/{file_name}")
async def tenders_download(
    file_name: str,
    db: DbSession,
    user: CurrentUser,
    download_name: str | None = None,
):
    del db, user
    path = resolve_output_file(file_name)
    name = (download_name or path.name).strip().replace("\\", "/").split("/")[-1]
    name = re.sub(r'[\r\n"]+', "", name) or path.name
    return FileResponse(
        path,
        media_type=_MIME.get(path.suffix.lower(), "application/octet-stream"),
        filename=name,
    )


@router.get("/files/{file_name}/editor-config")
async def tenders_editor_config(
    file_name: str,
    db: DbSession,
    user: CurrentUser,
    download_name: str | None = None,
    height: int | None = Query(None, ge=400, le=3000, description="编辑器高度（px，随视口传入）"),
    mode: str = Query("edit", description="edit=可编辑；view=只读预览（更快）"),
):
    """OnlyOffice 在线 Word 编辑器配置（需登录）。"""
    del db
    if not onlyoffice_enabled():
        raise AppError(
            ErrorCode.BAD_REQUEST,
            "OnlyOffice 未配置，请设置 ONLYOFFICE_DOCUMENT_SERVER_URL 并启动 Document Server",
            status_code=503,
        )
    resolve_output_file(file_name)
    settings = get_settings()
    display = (download_name or file_name).strip().replace("\\", "/").split("/")[-1]
    display = re.sub(r'[\r\n"]+', "", display) or file_name
    config = build_editor_config(
        file_name,
        download_name=display,
        user_id=str(user.id),
        user_name=user.username or user.display_name or "用户",
        editor_height_px=height,
        mode=mode,
    )
    return ok(
        {
            "documentServerUrl": settings.onlyoffice_document_server_url.rstrip("/"),
            "config": config,
        }
    )


@router.get("/files/{file_name}/onlyoffice-download")
async def tenders_onlyoffice_download(
    file_name: str,
    token: str,
    db: DbSession,
):
    """OnlyOffice 容器拉取 docx（短效 token，不走用户 JWT）。"""
    del db
    if not verify_download_token(token, file_name):
        raise AppError(ErrorCode.UNAUTHORIZED, "invalid or expired download token", status_code=401)
    path = resolve_output_file(file_name)
    return FileResponse(
        path,
        media_type=_MIME.get(path.suffix.lower(), "application/octet-stream"),
        filename=path.name,
    )


@router.post("/files/{file_name}/onlyoffice-callback")
async def tenders_onlyoffice_callback(
    file_name: str,
    request: Request,
    db: DbSession,
):
    """OnlyOffice 保存回调（须返回 ``{"error": 0}``，不能包 envelope）。"""
    del db
    resolve_output_file(file_name)
    try:
        body = await request.json()
    except Exception as exc:
        raise AppError(ErrorCode.BAD_REQUEST, "invalid callback body", status_code=400) from exc
    if not isinstance(body, dict):
        raise AppError(ErrorCode.BAD_REQUEST, "invalid callback body", status_code=400)
    result = await handle_callback(file_name, body)
    return JSONResponse(content=result)


def _coerce_placeholders(raw) -> list[PlaceholderItem]:
    out: list[PlaceholderItem] = []
    for item in raw or []:
        try:
            if isinstance(item, PlaceholderItem):
                out.append(item)
            elif isinstance(item, dict):
                out.append(PlaceholderItem.model_validate(item))
        except Exception:
            continue
    return out


def _parse_extras(raw: str | None) -> list[PlaceholderItem] | None:
    if raw is None or not str(raw).strip():
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AppError(ErrorCode.VALIDATION, "extras 必须是 JSON 数组", status_code=422) from exc
    if not isinstance(data, list):
        raise AppError(ErrorCode.VALIDATION, "extras 必须是 JSON 数组", status_code=422)
    return _coerce_placeholders(data)


def _parse_current_brief(raw: str | None) -> BidBrief | None:
    if raw is None or not str(raw).strip():
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AppError(ErrorCode.VALIDATION, "current 必须是 JSON 对象", status_code=422) from exc
    if not isinstance(data, dict):
        raise AppError(ErrorCode.VALIDATION, "current 必须是 JSON 对象", status_code=422)
    try:
        return BidBrief.model_validate(data)
    except Exception as exc:
        raise AppError(ErrorCode.VALIDATION, f"当前表单无法解析：{exc}", status_code=422) from exc
