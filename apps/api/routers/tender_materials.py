"""Independent authenticated archive APIs, mounted under /tenders/materials."""
from fastapi import APIRouter, File, Form, Query, UploadFile
from fastapi.responses import FileResponse

from api.deps import CurrentUser, DbSession, user_is_admin
from api.services.knowledge import access as kb_access
from api.services.knowledge.ingest import get_document_preview, resolve_document_file
from api.services.knowledge.parsers import extract_text_from_file
from api.services.menus import can_access_menu
from api.services.tenders import material_store as store
from api.services.tenders.schema import BidBrief
from common.errors import AppError, ErrorCode
from common.response import ok

router = APIRouter(prefix='/materials', tags=['tender materials'])


def allow(user):
    if not (user_is_admin(user) or can_access_menu(user, '/tenders')
            or can_access_menu(user, '/tender-library')):
        raise AppError(ErrorCode.FORBIDDEN, '无权限访问资料归档', status_code=403)


@router.get('')
async def list_archives(user: CurrentUser, invitationId: str = Query('')):
    allow(user)
    return ok(store.list_materials(int(user.id), invitationId))


@router.post('')
async def upload_archive(user: CurrentUser, category: str = Form(...),
                         subtype: str = Form(...), entity: str = Form(''),
                         bidderName: str = Form(''),
                         invitationId: str = Form(''), file: UploadFile = File(...)):
    allow(user)
    data = await file.read(store.MAX_BYTES + 1)
    return ok(await store.upload_material(int(user.id), invitationId, category,
               filename=file.filename, data=data, entity=entity, subtype=subtype, bidder_name=bidderName))


@router.post('/preflight')
async def check_archives(body: BidBrief, user: CurrentUser):
    allow(user)
    return ok(store.preflight(int(user.id), body))


@router.post('/sections')
async def archive_sections(body: BidBrief, user: CurrentUser):
    allow(user)
    return ok({'sections': store.existing_sections(body)})


@router.get('/library-preview/{doc_id}')
async def preview_library_document(doc_id: str, db: DbSession, user: CurrentUser):
    """返回投标资料库 Office 文件已提取的正文，供页面只读预览。"""
    allow(user)
    await kb_access.require_tender_lib_read(db, user)
    data = await get_document_preview(
        db,
        base_public_id=kb_access.TENDER_LIB_PUBLIC_ID,
        doc_public_id=doc_id,
    )
    content = str(data.get('content') or '').strip()
    truncated = bool(data.get('truncated'))
    # OCR/vector ingestion is asynchronous. For Office files, parse on demand
    # so a newly uploaded document is still previewable before the queue drains.
    if not content or content == '正在解析…':
        try:
            path, _filename, _media_type = await resolve_document_file(
                db,
                base_public_id=kb_access.TENDER_LIB_PUBLIC_ID,
                doc_public_id=doc_id,
            )
            if path.suffix.lower() in {'.doc', '.docx', '.xls', '.xlsx'}:
                extracted = await extract_text_from_file(
                    path,
                    ext=path.suffix.lstrip('.'),
                    ocr_budget=0,
                )
                content = extracted.text.strip()
                truncated = False
        except Exception:
            # The async ingestion result remains the source of truth; a
            # transient parse failure should leave the download path usable.
            pass
    return ok({
        'content': content,
        'truncated': truncated,
    })


@router.get('/{category}/{file_id}')
async def download_archive(category: str, file_id: str, user: CurrentUser,
                           invitationId: str = Query('')):
    allow(user)
    path, item = store.material_path(int(user.id), invitationId, category, file_id)
    return FileResponse(path, filename=item['filename'], media_type='application/octet-stream')


@router.delete('/{category}/{file_id}')
async def remove_archive(category: str, file_id: str, user: CurrentUser,
                         invitationId: str = Query('')):
    allow(user)
    return ok(store.delete_material(int(user.id), invitationId, category, file_id))
