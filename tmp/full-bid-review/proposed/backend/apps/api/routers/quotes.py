"""AI 造价 / 报价 / 预算：识别、工程量解析、核算、导出。"""

from __future__ import annotations

import logging
import re
from decimal import Decimal

from fastapi import APIRouter, File, Form, Query, UploadFile
from fastapi.responses import FileResponse

from api.deps import CurrentUser, DbSession, user_is_admin
from api.services.menus import can_access_menu
from api.services.quotes.assemble import (
    apply_catalog,
    catalog_from_docs,
    number_lines,
    pending_price_count,
    recognize_from_bom,
    _dump_line,
)
from api.services.quotes.boq import lines_from_boq_bytes
from api.services.quotes.catalog import list_catalog_hits
from api.services.quotes.contract import PURPOSE_LABEL, default_rates_dict, normalize_purpose
from api.services.quotes.costing import apply_costing
from api.services.quotes.decide import build_schemes
from api.services.quotes.excel import resolve_quote_file
from api.services.quotes.library import docs_for_quote_kb
from api.services.quotes.records import (
    can_see_cost,
    clear_mine,
    create_from_generate,
    delete_record,
    get_record,
    get_record_by_file,
    list_records,
    lock_record,
    _redact_lines,
    _redact_summary,
    rewrite_xlsx,
)
from api.services.quotes.schema import CatalogCandidatesIn, CostQuoteIn, GenerateQuoteIn, VerifyQuoteIn
from api.services.quotes.verify import apply_amount_fixes, verify_lines
from api.services.quotes.vision import MAX_RASTERS, MAX_SITE_MAPS, rasterize_upload, read_site_map
from common.errors import AppError, ErrorCode
from common.response import ok

router = APIRouter(prefix="/quotes", tags=["quotes"])
logger = logging.getLogger("api.quotes")

_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_QUOTE_MENUS = ("/quotes", "/quotes-cost", "/quotes-budget")


def _require_menu(user) -> None:
    if user_is_admin(user):
        return
    if any(can_access_menu(user, href) for href in _QUOTE_MENUS):
        return
    if can_access_menu(user, "/quotes"):
        return
    raise AppError(ErrorCode.FORBIDDEN, "无权限访问 AI 报价", status_code=403)


@router.get("/meta")
async def quotes_meta(user: CurrentUser):
    _require_menu(user)
    return ok(
        {
            "purposes": [
                {"id": "cost", "label": PURPOSE_LABEL["cost"], "href": "/quotes-cost"},
                {"id": "quote", "label": PURPOSE_LABEL["quote"], "href": "/quotes"},
                {"id": "budget", "label": PURPOSE_LABEL["budget"], "href": "/quotes-budget"},
            ],
            "defaultRates": default_rates_dict(),
            "stages": ["parsed", "verified", "costed", "quoted"],
        }
    )


@router.post("/recognize")
async def quotes_recognize(
    db: DbSession,
    user: CurrentUser,
    files: list[UploadFile] = File(..., description="场地规划图，图片或 PDF"),
    baseId: str = Form(..., min_length=1, max_length=32),
    note: str | None = Form(None, max_length=2000),
    projectName: str | None = Form(None, max_length=120),
    purpose: str | None = Form("quote"),
    location: str | None = Form(None, max_length=128),
    durationDays: float | None = Form(None),
):
    _require_menu(user)
    purpose = normalize_purpose(purpose)
    if len(files) > MAX_SITE_MAPS:
        raise AppError(
            ErrorCode.VALIDATION,
            f"规划图一次最多 {MAX_SITE_MAPS} 张",
            status_code=422,
        )
    blobs: list[tuple[str, bytes]] = []
    for up in files:
        if len(blobs) >= MAX_RASTERS:
            break
        raw = await up.read()
        blobs.extend(
            rasterize_upload(
                up.filename or "",
                up.content_type,
                raw,
                remaining=MAX_RASTERS - len(blobs),
            )
        )
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
    data["purpose"] = purpose
    data["stage"] = "parsed"
    data["location"] = (location or "").strip()
    data["durationDays"] = durationDays
    data["verify"] = verify_lines(data.get("lines") or [])
    data["stage"] = "verified"
    data["unmatched"] = pending_price_count(data.get("lines") or [])
    if purpose == "quote" and not can_see_cost(user):
        data["lines"] = _redact_lines(data.get("lines") or [])
    logger.info(
        "quote recognize user=%s purpose=%s kb=%s lines=%s unmatched=%s",
        user.username,
        purpose,
        kb_id,
        len(data.get("lines") or []),
        data.get("unmatched"),
    )
    return ok(data)


@router.post("/parse-boq")
async def quotes_parse_boq(
    db: DbSession,
    user: CurrentUser,
    file: UploadFile = File(..., description="工程量 Excel"),
    baseId: str = Form(..., min_length=1, max_length=32),
    purpose: str | None = Form("quote"),
    projectName: str | None = Form(None, max_length=120),
    location: str | None = Form(None, max_length=128),
    durationDays: float | None = Form(None),
):
    _require_menu(user)
    purpose = normalize_purpose(purpose)
    raw = await file.read()
    lines, warnings = lines_from_boq_bytes(raw, filename=file.filename or "boq.xlsx")
    kb_id, kb_name, docs = await docs_for_quote_kb(db, user, baseId)
    catalog, cat_err, meta = catalog_from_docs(docs)
    warnings.extend(cat_err)
    # 有单价的工程量行保留；无单价的对价目
    need = [r for r in lines if not (float(r.get("unitPrice") or 0) > 0)]
    if need:
        warnings.extend(apply_catalog(need, catalog))
    for r in lines:
        if float(r.get("unitPrice") or 0) > 0 and not str(r.get("matchStatus") or "").strip():
            r["matchStatus"] = "official"
    lines = number_lines(lines)
    payload = {
        "purpose": purpose,
        "stage": "verified",
        "projectName": (projectName or "").strip(),
        "location": (location or "").strip(),
        "durationDays": durationDays,
        "lines": [_dump_line(r) for r in lines],
        "warnings": warnings,
        "catalogCount": len(catalog),
        "unmatched": pending_price_count(lines),
        "catalog": meta,
        "baseId": kb_id,
        "baseName": kb_name,
        "verify": verify_lines([_dump_line(r) for r in lines]),
    }
    if purpose == "quote" and not can_see_cost(user):
        payload["lines"] = _redact_lines(payload["lines"])
    return ok(payload)


@router.post("/verify")
async def quotes_verify(body: VerifyQuoteIn, user: CurrentUser):
    _require_menu(user)
    rows = [ln.model_dump() for ln in body.lines if str(ln.name or "").strip()]
    report = verify_lines(rows)
    if body.applyFixes:
        rows = apply_amount_fixes(rows, report)
        report = verify_lines(rows)
    return ok({"verify": report, "lines": rows if body.applyFixes else None})


@router.post("/cost")
async def quotes_cost(body: CostQuoteIn, user: CurrentUser):
    _require_menu(user)
    rows = [ln.model_dump() for ln in body.lines if str(ln.name or "").strip()]
    rates = body.rates.model_dump() if body.rates else default_rates_dict()
    costed, summary = apply_costing(rows, rates)
    schemes = []
    if normalize_purpose(body.purpose) == "quote":
        schemes, _, _ = build_schemes(
            cost_ex=Decimal(str(summary["costExTax"])),
            quote_ex=Decimal(str(summary["quoteExTax"])),
            bid_ceiling=body.bidCeiling,
            competition=body.competition or "balanced",
            target_margin=body.targetMargin,
            project_name=body.projectName,
            location=body.location or "",
            duration_days=body.durationDays,
        )
    dumped = [_dump_line(r) for r in costed]
    if body.purpose == "quote" and not can_see_cost(user):
        dumped = _redact_lines(dumped)
        summary = _redact_summary(summary) or summary
    return ok(
        {
            "stage": "costed",
            "lines": dumped,
            "costSummary": summary,
            "verify": verify_lines(costed),
            "schemes": schemes,
        }
    )


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
        "quote generate user=%s purpose=%s id=%s lines=%s",
        user.username,
        body.purpose,
        payload.get("id"),
        payload.get("lineCount"),
    )
    if body.purpose == "quote" and not can_see_cost(user):
        if isinstance(payload.get("costSummary"), dict):
            payload["costSummary"] = _redact_summary(payload["costSummary"])
        payload["seeCost"] = False
    else:
        payload["seeCost"] = True
    return ok(payload)


@router.post("/catalog-candidates")
async def quotes_catalog_candidates(
    body: CatalogCandidatesIn,
    db: DbSession,
    user: CurrentUser,
):
    _require_menu(user)
    _kb_id, kb_name, docs = await docs_for_quote_kb(db, user, body.baseId)
    catalog, errors, meta = catalog_from_docs(docs)
    hits = list_catalog_hits(
        name=body.name,
        spec=body.spec,
        code=body.code,
        catalog=catalog,
        limit=body.limit,
    )
    if not can_see_cost(user):
        hits = [{**h, "costPrice": 0} for h in hits]
    return ok({"items": hits, "baseName": kb_name, "catalog": meta, "errors": errors})


@router.get("/records")
async def quotes_list_records(
    db: DbSession,
    user: CurrentUser,
    q: str | None = Query(None),
    purpose: str | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    _require_menu(user)
    return ok(
        await list_records(
            db,
            user_id=int(user.id),
            q=q,
            purpose=purpose,
            status=status,
            limit=limit,
            offset=offset,
            see_cost=can_see_cost(user),
        )
    )


@router.post("/records/clear-mine")
async def quotes_clear_mine(
    db: DbSession,
    user: CurrentUser,
    purpose: str | None = Query(None),
):
    _require_menu(user)
    return ok(await clear_mine(db, user=user, purpose=purpose))


@router.get("/records/{record_id}")
async def quotes_get_record(record_id: str, db: DbSession, user: CurrentUser):
    _require_menu(user)
    return ok(await get_record(db, record_id, user=user, admin=user_is_admin(user)))


@router.post("/records/{record_id}/export")
async def quotes_rewrite_record(record_id: str, db: DbSession, user: CurrentUser):
    _require_menu(user)
    return ok(await rewrite_xlsx(db, record_id, user=user, admin=user_is_admin(user)))


@router.post("/records/{record_id}/lock")
async def quotes_lock_record(record_id: str, db: DbSession, user: CurrentUser):
    _require_menu(user)
    return ok(await lock_record(db, record_id, user=user, admin=user_is_admin(user)))


@router.post("/records/{record_id}/unlock")
async def quotes_unlock_record(record_id: str, db: DbSession, user: CurrentUser):
    _require_menu(user)
    return ok(await lock_record(db, record_id, user=user, admin=user_is_admin(user), unlock=True))


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
    _require_menu(user)
    await get_record_by_file(db, file_name, user=user, admin=user_is_admin(user))
    path = resolve_quote_file(file_name)
    name = (download_name or path.name).strip().replace("\\", "/").split("/")[-1]
    name = re.sub(r'[\r\n"]+', "", name) or path.name
    return FileResponse(path, media_type=_XLSX_MIME, filename=name)
