"""投标文件：邀请书抽取、填表生成 Word，下载 docx / 资质 PDF；待补附件上传。"""

from __future__ import annotations

import asyncio
import json
import logging
import re

from fastapi import APIRouter, File, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from api.deps import CurrentUser, DbSession, SuperuserUser, user_is_admin, user_is_superuser
from api.schemas.approval_flow import ApprovalFlowUpdateIn
from api.services.approval_flow import get_tender_flow, save_tender_flow
from api.services.knowledge import access as kb_access
from api.services.menus import can_access_menu
from api.services.tenders.assets import restore_chapter5_template, save_chapter5_template
from api.services.tenders.extract import parse_invitation, parse_kb_ids
from api.services.tenders.generate import defaults_payload_kb, resolve_output_file
from api.services.tenders.library_kb import (
    clear_library_files,
    create_library_item,
    delete_library_file,
    delete_library_item,
    enqueue_library_rag_backfill,
    library_payload_kb,
    list_slots_status_kb,
    open_library_file,
    save_library_file,
    update_library_item,
)
from api.services.tenders.onlyoffice import (
    build_editor_config,
    handle_callback,
    onlyoffice_enabled,
    verify_download_token,
)
from api.services.tenders.placeholders import TECH_DRAWING_KEY
from api.services.tenders.preview_pdf import ensure_preview_pdf
from api.services.tenders.records import (
    ACTION_PASS,
    ACTION_REJECT,
    clear_mine_records,
    create_record_from_generate,
    decide_approval,
    delete_record,
    get_record,
    get_record_qa,
    inspect_record_qa,
    inspect_record_qa_upload,
    list_records,
    mark_result,
    record_ids_for_actor,
    regenerate_from_record,
    submit_for_approval,
)
from api.services.tenders.schema import BidBrief, PlaceholderItem
from api.services.tenders.slots import clear_drawing_files, sanitize_slot_key, save_drawing_file
from api.services.tenders.yozo import (
    build_editor_payload as build_yozo_payload,
)
from api.services.tenders.yozo import (
    handle_callback as handle_yozo_callback,
)
from api.services.tenders.yozo import (
    yozo_enabled,
)
from common.config import get_settings
from common.errors import AppError, ErrorCode
from common.response import ok

router = APIRouter(prefix="/tenders", tags=["tenders"])

logger = logging.getLogger("api.tenders")

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


class ApprovalDecisionIn(BaseModel):
    passed: bool
    comment: str = ""


class MarkResultIn(BaseModel):
    status: str = Field(..., min_length=1, max_length=16)


def _require_menu(user, href: str) -> None:
    if user_is_admin(user) or can_access_menu(user, href):
        return
    raise AppError(ErrorCode.FORBIDDEN, "无权限访问该功能", status_code=403)


def _require_qa_menu(user) -> None:
    if (
        user_is_admin(user)
        or can_access_menu(user, "/tenders")
        or can_access_menu(user, "/tender-qa")
    ):
        return
    raise AppError(ErrorCode.FORBIDDEN, "无权限访问该功能", status_code=403)


@router.get("/defaults")
async def tenders_defaults(db: DbSession, user: CurrentUser):
    del user
    return ok(await defaults_payload_kb(db))


@router.get("/approval-flow")
async def tenders_get_approval_flow(db: DbSession, user: CurrentUser):
    _require_menu(user, "/approval")
    payload = await get_tender_flow(db, can_edit=user_is_superuser(user))
    if payload.get("canEdit"):
        from api.services import users as users_svc

        payload["roles"] = await users_svc.list_roles(db)
    return ok(payload)


@router.put("/approval-flow")
async def tenders_put_approval_flow(
    body: ApprovalFlowUpdateIn,
    db: DbSession,
    user: SuperuserUser,
):
    return ok(
        await save_tender_flow(
            db,
            user=user,
            reviews=[item.model_dump() for item in body.reviews],
        )
    )


@router.post("/layout-template")
async def tenders_upload_layout_template(user: CurrentUser, file: UploadFile = File(...)):
    """更换公司固定投标文件空白稿（.docx）。覆盖 storage/tender-assets/chapter5.docx。"""
    del user
    name = (file.filename or "").lower()
    if not name.endswith(".docx"):
        raise AppError(ErrorCode.VALIDATION, "请上传 Word 空白稿（.docx）", status_code=422)
    data = await file.read()
    return ok(save_chapter5_template(data))


@router.delete("/layout-template")
async def tenders_restore_layout_template(user: CurrentUser):
    del user
    return ok(restore_chapter5_template())


@router.get("/library")
async def tenders_library(db: DbSession, user: CurrentUser):
    """投标资料库：知识库中的可维护扫描件清单。"""
    await kb_access.require_tender_lib_read(db, user)
    return ok(await library_payload_kb(db))


@router.post("/library/reindex")
async def tenders_library_reindex(
    db: DbSession,
    user: CurrentUser,
    force: bool = Query(default=False, description="true=强制重新 OCR 入库"),
):
    """将资料库扫描件 OCR 向量化，供 AI 智能问答检索。"""
    await kb_access.require_tender_lib_manage(db, user)
    return ok(await enqueue_library_rag_backfill(db, force=bool(force)))


@router.get("/library/files/{doc_id}")
async def tenders_library_file(
    doc_id: str,
    db: DbSession,
    user: CurrentUser,
    thumb: bool = Query(False, description="true=缩略图 JPEG"),
):
    await kb_access.require_tender_lib_read(db, user)
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
    await kb_access.require_tender_lib_manage(db, user)
    return ok(await delete_library_file(db, doc_id))


@router.post("/library/items")
async def tenders_create_library_item(body: LibraryItemIn, db: DbSession, user: CurrentUser):
    await kb_access.require_tender_lib_manage(db, user)
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
    await kb_access.require_tender_lib_manage(db, user)
    return ok(await update_library_item(db, key, title=body.title, hint=body.hint))


@router.delete("/library/items/{key}")
async def tenders_delete_library_item(key: str, db: DbSession, user: CurrentUser):
    await kb_access.require_tender_lib_manage(db, user)
    return ok(await delete_library_item(db, key))


@router.get("/slots")
async def tenders_list_slots(
    db: DbSession,
    user: CurrentUser,
    extras: str | None = None,
    invitationId: str | None = Query(None),
):
    """待补附件槽位状态。extras 为可选 JSON 数组（邀请书多出来的项）。"""
    await kb_access.require_tender_lib_read(db, user)
    return ok(
        {
            "slots": await list_slots_status_kb(
                db,
                _parse_extras(extras),
                invitation_id=invitationId or "",
            )
        }
    )


@router.post("/slots/{key}")
async def tenders_upload_slot(
    key: str,
    db: DbSession,
    user: CurrentUser,
    file: UploadFile = File(..., description="扫描件：pdf / png / jpg / webp / gif / bmp"),
    replace: str | None = Form("true", description="true=替换该项全部文件；false=追加"),
    invitationId: str | None = Form(None),
):
    safe = sanitize_slot_key(key)
    raw = await file.read()
    do_replace = str(replace or "true").strip().lower() not in {"0", "false", "no"}
    if safe == TECH_DRAWING_KEY:
        return ok(
            save_drawing_file(
                invitation_id=invitationId or "",
                filename=file.filename or "upload.bin",
                data=raw,
                replace=do_replace,
            )
        )
    await kb_access.require_tender_lib_manage(db, user)
    payload = await save_library_file(
        db,
        safe,
        filename=file.filename or "upload.bin",
        data=raw,
        replace=do_replace,
    )
    return ok(payload)


@router.delete("/slots/{key}")
async def tenders_clear_slot(
    key: str,
    db: DbSession,
    user: CurrentUser,
    invitationId: str | None = Query(None),
):
    safe = sanitize_slot_key(key)
    if safe == TECH_DRAWING_KEY:
        return ok(clear_drawing_files(invitationId or ""))
    await kb_access.require_tender_lib_manage(db, user)
    return ok(await clear_library_files(db, safe))


@router.post("/parse-invitation")
async def tenders_parse_invitation(
    db: DbSession,
    user: CurrentUser,
    file: UploadFile = File(..., description="投标邀请书 / 招标文件：pdf / docx / xlsx / txt / 图片"),
    quoteFile: UploadFile | None = File(None, description="可选：工程量清单 Excel/Word"),
    quoteRecordIds: str | None = Form(
        None, description="可选：AI 报价记录 publicId，JSON 数组或逗号分隔"
    ),
    kbIds: str | None = Form(None, description="知识库 ID：JSON 数组或逗号分隔"),
    current: str | None = Form(None, description="当前表单 JSON，抽取结果合并到其上"),
):
    brief = _parse_current_brief(current)
    persist_library = await kb_access.has_base_perm(
        db, user, kb_access.TENDER_LIB_PUBLIC_ID, kb_access.PERM_MANAGE
    )
    payload = await parse_invitation(
        db,
        file,
        kb_ids=parse_kb_ids(kbIds),
        current=brief,
        quote_upload=quoteFile,
        quote_record_ids=parse_kb_ids(quoteRecordIds),
        created_by=int(user.id),
        persist_library=persist_library,
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
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    scope: str = Query("mine", description="mine=本人；all=全部"),
    status: str | None = Query(None, description="工作流状态"),
    owner: str | None = Query(None, description="创建人用户名"),
    projectType: str | None = Query(None, description="项目类型"),
    approvalTab: str | None = Query(None, description="pending/done/mine"),
):
    admin = user_is_admin(user)
    tab = (approvalTab or "").strip()
    record_ids = None
    submitted_only = False
    user_id = None
    if tab:
        _require_menu(user, "/approval")
        if tab == "pending":
            status = "pending"
        elif tab == "done":
            record_ids = await record_ids_for_actor(
                db, user_id=int(user.id), actions=(ACTION_PASS, ACTION_REJECT)
            )
        elif tab == "mine":
            user_id = int(user.id)
            submitted_only = True
        else:
            raise AppError(ErrorCode.VALIDATION, "approvalTab 无效", status_code=422)
    else:
        want_all = (scope or "mine").strip() == "all"
        if want_all:
            if not (
                admin
                or can_access_menu(user, "/tender-tasks")
                or can_access_menu(user, "/approval")
            ):
                raise AppError(ErrorCode.FORBIDDEN, "无权查看全部投标任务", status_code=403)
        else:
            user_id = int(user.id)
    return ok(
        await list_records(
            db,
            q=q,
            limit=limit,
            offset=offset,
            status=status,
            owner=owner,
            user_id=user_id,
            project_type=projectType,
            record_ids=record_ids,
            submitted_only=submitted_only,
            pending_for=user if tab == "pending" else None,
        )
    )


@router.post("/records/clear-mine")
async def tenders_clear_mine_records(db: DbSession, user: CurrentUser):
    """清空当前用户自己的生成记录，角标计数归零。"""
    return ok(await clear_mine_records(db, user=user, admin=user_is_admin(user)))


@router.get("/records/{record_id}")
async def tenders_get_record(record_id: str, db: DbSession, user: CurrentUser):
    del user
    return ok(await get_record(db, record_id))


@router.get("/records/{record_id}/qa")
async def tenders_get_record_qa(
    record_id: str,
    db: DbSession,
    user: CurrentUser,
    volume: str | None = Query(default=None),
):
    _require_qa_menu(user)
    vol = (volume or "").strip().lower()
    if vol and vol not in {"business", "technical"}:
        raise AppError(ErrorCode.VALIDATION, "volume 只能是 business 或 technical", status_code=422)
    return ok(await get_record_qa(db, record_id, volume=vol or None))


@router.post("/records/{record_id}/qa")
async def tenders_inspect_record_qa(
    record_id: str,
    db: DbSession,
    user: CurrentUser,
    volume: str | None = Query(default=None),
):
    """生成后对照邀请书做 AI 质检：符合度与缺失项。"""
    _require_qa_menu(user)
    vol = (volume or "").strip().lower()
    if vol and vol not in {"business", "technical"}:
        raise AppError(ErrorCode.VALIDATION, "volume 只能是 business 或 technical", status_code=422)
    return ok(await inspect_record_qa(db, record_id, volume=vol or None))


@router.post("/records/{record_id}/qa-upload")
async def tenders_inspect_record_qa_upload(
    record_id: str,
    db: DbSession,
    user: CurrentUser,
    file: UploadFile = File(..., description="改过的终稿 Word（.docx）"),
    volume: str | None = Query(default=None),
):
    """上传终稿 Word，对照本任务邀请书做 AI 复检。"""
    _require_qa_menu(user)
    name = (file.filename or "").strip()
    if not name.lower().endswith(".docx"):
        raise AppError(ErrorCode.VALIDATION, "请上传 Word（.docx）", status_code=422)
    vol = (volume or "").strip().lower()
    if vol and vol not in {"business", "technical"}:
        raise AppError(ErrorCode.VALIDATION, "volume 只能是 business 或 technical", status_code=422)
    data = await file.read()
    return ok(
        await inspect_record_qa_upload(
            db,
            record_id,
            data=data,
            filename=name,
            volume=vol or None,
        )
    )


@router.delete("/records/{record_id}")
async def tenders_delete_record(record_id: str, db: DbSession, user: CurrentUser):
    return ok(await delete_record(db, record_id, user=user, admin=user_is_admin(user)))


@router.post("/records/{record_id}/regenerate")
async def tenders_regenerate_record(
    record_id: str,
    db: DbSession,
    user: CurrentUser,
    request: Request,
):
    """覆盖同一条记录的 Word，不重置审批状态。可传当前表单；缺省用保存快照。"""
    raw = (await request.body() or b"").strip()
    brief = None
    if raw:
        try:
            brief = BidBrief.model_validate(json.loads(raw))
        except Exception as exc:
            raise AppError(ErrorCode.VALIDATION, f"表单无法解析：{exc}", status_code=422) from exc
    payload = await regenerate_from_record(
        db,
        record_id,
        user=user,
        admin=user_is_admin(user),
        brief=brief,
    )
    return ok(payload)


@router.post("/records/{record_id}/submit")
async def tenders_submit_record(record_id: str, db: DbSession, user: CurrentUser):
    return ok(
        await submit_for_approval(db, record_id, user=user, admin=user_is_admin(user))
    )


@router.post("/records/{record_id}/decide")
async def tenders_decide_record(
    record_id: str,
    body: ApprovalDecisionIn,
    db: DbSession,
    user: CurrentUser,
):
    _require_menu(user, "/approval")
    return ok(
        await decide_approval(
            db,
            record_id,
            user=user,
            passed=body.passed,
            comment=body.comment or "",
        )
    )


@router.post("/records/{record_id}/mark")
async def tenders_mark_record(
    record_id: str,
    body: MarkResultIn,
    db: DbSession,
    user: CurrentUser,
):
    return ok(
        await mark_result(
            db,
            record_id,
            user=user,
            admin=user_is_admin(user),
            status=body.status,
        )
    )


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


@router.get("/files/{file_name}/preview-pdf")
async def tenders_preview_pdf(
    file_name: str,
    db: DbSession,
    user: CurrentUser,
):
    """本机 WPS/Word 排版后的 PDF，只读预览用。"""
    del db, user
    path = resolve_output_file(file_name)
    if path.suffix.lower() != ".docx":
        raise AppError(ErrorCode.BAD_REQUEST, "仅支持预览 Word 文件", status_code=400)
    pdf = await asyncio.to_thread(ensure_preview_pdf, path)
    return FileResponse(
        pdf,
        media_type="application/pdf",
        filename="preview.pdf",
        content_disposition_type="inline",
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
    """在线预览/改稿配置。browser=docx-preview；onlyoffice / yozo 走文档服务。"""
    del db
    settings = get_settings()
    resolve_output_file(file_name)
    display = (download_name or file_name).strip().replace("\\", "/").split("/")[-1]
    display = re.sub(r'[\r\n"]+', "", display) or file_name
    user_name = user.username or user.display_name or "用户"
    engine = settings.doc_preview_engine
    if engine == "yozo":
        if not yozo_enabled(settings):
            raise AppError(
                ErrorCode.BAD_REQUEST,
                "永中 Web Office 未配置，请设置 YOZO_DOCUMENT_SERVER_URL",
                status_code=503,
            )
        return ok(
            build_yozo_payload(
                file_name,
                download_name=display,
                user_id=str(user.id),
                user_name=user_name,
                mode=mode,
                settings=settings,
            )
        )
    if engine != "onlyoffice":
        return ok({"engine": "browser", "documentServerUrl": "", "iframeUrl": "", "config": {}})
    if not onlyoffice_enabled():
        raise AppError(
            ErrorCode.BAD_REQUEST,
            "OnlyOffice 未配置，请设置 ONLYOFFICE_DOCUMENT_SERVER_URL 并启动 Document Server",
            status_code=503,
        )
    config = build_editor_config(
        file_name,
        download_name=display,
        user_id=str(user.id),
        user_name=user_name,
        editor_height_px=height,
        mode=mode,
    )
    return ok(
        {
            "engine": "onlyoffice",
            "documentServerUrl": settings.onlyoffice_document_server_url.rstrip("/"),
            "iframeUrl": "",
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
        logger.warning("onlyoffice download rejected file=%s", file_name)
        raise AppError(ErrorCode.UNAUTHORIZED, "invalid or expired download token", status_code=401)
    path = resolve_output_file(file_name)
    logger.info("onlyoffice download ok file=%s bytes=%s", file_name, path.stat().st_size)
    # 不要带 filename=：attachment 的 Content-Disposition 会让 Document Server 拉取失败。
    return FileResponse(
        path,
        media_type=_MIME.get(path.suffix.lower(), "application/octet-stream"),
        headers={"Cache-Control": "no-store", "Content-Disposition": "inline"},
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


@router.get("/files/{file_name}/yozo-download")
async def tenders_yozo_download(
    file_name: str,
    token: str,
    db: DbSession,
):
    """永中服务器拉取 docx（短效 token，不走用户 JWT）。"""
    del db
    if not verify_download_token(token, file_name):
        logger.warning("yozo download rejected file=%s", file_name)
        raise AppError(ErrorCode.UNAUTHORIZED, "invalid or expired download token", status_code=401)
    path = resolve_output_file(file_name)
    logger.info("yozo download ok file=%s bytes=%s", file_name, path.stat().st_size)
    return FileResponse(
        path,
        media_type=_MIME.get(path.suffix.lower(), "application/octet-stream"),
        headers={"Cache-Control": "no-store", "Content-Disposition": "inline"},
    )


@router.post("/files/{file_name}/yozo-callback")
async def tenders_yozo_callback(
    file_name: str,
    request: Request,
    db: DbSession,
):
    """永中保存回调（须返回 errorCode=0，不能包 envelope）。"""
    del db
    resolve_output_file(file_name)
    result = await handle_yozo_callback(file_name, request)
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
