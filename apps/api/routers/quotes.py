"""AI 报价：规划图识别、价目知识库、导出 Excel。"""

from __future__ import annotations

import logging
import re

from fastapi import APIRouter, File, Form, Query, UploadFile
from fastapi.responses import FileResponse

from api.deps import CurrentUser, DbSession, user_is_admin
from api.services.menus import can_access_menu
from api.services.quotes.assemble import recognize_from_bom
from api.services.quotes.excel import resolve_quote_file
from api.services.quotes.library import docs_for_quote_kb
from api.services.quotes.records import (
    clear_mine,
    create_from_generate,
    delete_record,
    get_record,
    list_records,
    rewrite_xlsx,
)
from api.services.quotes.schema import GenerateQuoteIn
from api.services.quotes.vision import MAX_SITE_MAPS, rasterize_upload, read_site_map
from common.errors import AppError, ErrorCode
from common.response import ok

router = APIRouter(prefix="/quotes", tags=["quotes"])
logger = logging.getLogger("api.quotes")

_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _require_menu(user) -> None:
    if user_is_admin(user) or can_access_menu(user, "/quotes"):
        return
    raise AppError(ErrorCode.FORBIDDEN, "无权限访问 AI 报价", status_code=403)


@router.post("/recognize")
async def quotes_recognize(
    db: DbSession,
    user: CurrentUser,
    files: list[UploadFile] = File(..., description="场地规划图，图片或 PDF"),
    baseId: str = Form(..., min_length=1, max_length=32),
    note: str | None = Form(None, max_length=2000),
    projectName: str | None = Form(None, max_length=120),
):
    _require_menu(user)
    if len(files) > MAX_SITE_MAPS:
        raise AppError(
            ErrorCode.VALIDATION,
            f"规划图一次最多 {MAX_SITE_MAPS} 张",
            status_code=422,
        )
    blobs: list[tuple[str, bytes]] = []
    for up in files:
        raw = await up.read()
        blobs.append(rasterize_upload(up.filename or "", up.content_type, raw))
    if not blobs:
        raise AppError(ErrorCode.VALIDATION, "请上传场地规划图", status_code=422)
    bom = await read_site_map(db, blobs, note=note or "")
    kb_id, kb_name, docs = await docs_for_quote_kb(db, user, baseId)
    data = await recognize_from_bom(
        data=bom,
        docs=docs,
        project_name=projectName or "",
    )
    data["baseId"] = kb_id
    data["baseName"] = kb_name
    logger.info(
        "quote recognize user=%s kb=%s lines=%s unmatched=%s catalog=%s",
        user.username,
        kb_id,
        len(data.get("lines") or []),
        data.get("unmatched"),
        data.get("catalogCount"),
    )
    return ok(data)


@router.post("/generate")
async def quotes_generate(body: GenerateQuoteIn, db: DbSession, user: CurrentUser):
    _require_menu(user)
    payload = await create_from_generate(
        db,
        body,
        user_id=int(user.id),
        username=user.username or "",
    )
    logger.info(
        "quote generate user=%s id=%s lines=%s unmatched=%s",
        user.username,
        payload.get("id"),
        payload.get("lineCount"),
        payload.get("unmatched"),
    )
    return ok(payload)


@router.get("/records")
async def quotes_list_records(
    db: DbSession,
    user: CurrentUser,
    q: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    _require_menu(user)
    return ok(
        await list_records(
            db,
            user_id=int(user.id),
            q=q,
            limit=limit,
            offset=offset,
        )
    )


@router.post("/records/clear-mine")
async def quotes_clear_mine(db: DbSession, user: CurrentUser):
    _require_menu(user)
    return ok(await clear_mine(db, user=user))


@router.get("/records/{record_id}")
async def quotes_get_record(record_id: str, db: DbSession, user: CurrentUser):
    _require_menu(user)
    return ok(await get_record(db, record_id, user=user, admin=user_is_admin(user)))


@router.post("/records/{record_id}/export")
async def quotes_rewrite_record(record_id: str, db: DbSession, user: CurrentUser):
    _require_menu(user)
    return ok(await rewrite_xlsx(db, record_id, user=user, admin=user_is_admin(user)))


@router.delete("/records/{record_id}")
async def quotes_delete_record(record_id: str, db: DbSession, user: CurrentUser):
    _require_menu(user)
    return ok(await delete_record(db, record_id, user=user, admin=user_is_admin(user)))


@router.get("/files/{file_name}")
async def quotes_download(
    file_name: str,
    db: DbSession,
    user: CurrentUser,
    download_name: str | None = None,
):
    del db
    _require_menu(user)
    path = resolve_quote_file(file_name)
    name = (download_name or path.name).strip().replace("\\", "/").split("/")[-1]
    name = re.sub(r'[\r\n"]+', "", name) or path.name
    return FileResponse(path, media_type=_XLSX_MIME, filename=name)
