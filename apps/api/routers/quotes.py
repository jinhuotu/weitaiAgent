"""AI 报价：规划图识别、价目知识库、导出 Excel。"""

from __future__ import annotations

import logging
import re

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import FileResponse

from api.deps import CurrentUser, DbSession
from api.services.menus import can_access_menu, user_is_admin
from api.services.quotes.assemble import recognize_from_bom
from api.services.quotes.excel import resolve_quote_file, write_quote_xlsx
from api.services.quotes.library import docs_for_quote_kb
from api.services.quotes.schema import GenerateQuoteIn
from api.services.quotes.vision import rasterize_upload, read_site_map
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
    blobs: list[tuple[str, bytes]] = []
    for up in files[:4]:
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
        "quote recognize user=%s kb=%s lines=%s unmatched=%s",
        user.username,
        kb_id,
        len(data.get("lines") or []),
        data.get("unmatched"),
    )
    return ok(data)


@router.post("/generate")
async def quotes_generate(body: GenerateQuoteIn, db: DbSession, user: CurrentUser):
    del db
    _require_menu(user)
    path, download_name, total, inc = write_quote_xlsx(
        project_name=body.projectName,
        note=body.note,
        tax_rate=body.taxRate,
        lines=body.lines,
    )
    unmatched = sum(1 for ln in body.lines if ln.unitPrice <= 0 and str(ln.name or "").strip())
    return ok(
        {
            "xlsxFile": path.name,
            "downloadName": download_name,
            "totalExTax": float(total),
            "totalIncTax": float(inc),
            "unmatched": unmatched,
            "lineCount": len([ln for ln in body.lines if str(ln.name or "").strip()]),
        }
    )


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
