"""Preview and confirm bid response data without touching source files."""
import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi import APIRouter, File, Form, UploadFile
from pydantic import BaseModel, Field
from api.deps import CurrentUser
from api.services import bid_table_import as service
from api.services.tenders.assets import load_invitation_text
from common.errors import AppError, ErrorCode
from common.response import ok

router = APIRouter(prefix='/table-imports', tags=['tender-table-imports'])


@router.post('/preview')
async def preview(user: CurrentUser, files: list[UploadFile] = File(...), invitationId: str = Form(...)):
    if not 1 <= len(files) <= 2 or not load_invitation_text(invitationId):
        raise AppError(ErrorCode.VALIDATION, '请先识别招标文件，再上传一份Excel和/或一份Word', status_code=422)
    extensions = [Path(f.filename or '').suffix.lower() for f in files]
    if any(e not in {'.xlsx', '.docx'} for e in extensions) or len(set(extensions)) != len(extensions):
        raise AppError(ErrorCode.VALIDATION, '仅支持一份XLSX和一份DOCX', status_code=422)
    sources = []
    with TemporaryDirectory(prefix='bid-import-') as folder:
        for i, upload in enumerate(files):
            data = await upload.read(30 * 1024 * 1024 + 1)
            if not data or len(data) > 30 * 1024 * 1024:
                raise AppError(ErrorCode.VALIDATION, '单个文件须为非空且不超过30MB', status_code=422)
            path = Path(folder) / f'{i}{extensions[i]}'
            path.write_bytes(data)
            try:
                sources.append(await asyncio.to_thread(service.parse_file, path, Path(upload.filename).name))
            except Exception as exc:
                raise AppError(ErrorCode.VALIDATION, f'无法解析文件：{upload.filename}', status_code=422) from exc
    merged = service.reconcile(sources)
    tender_no = merged['identity'].get('招标编号')
    if tender_no and tender_no not in load_invitation_text(invitationId):
        raise AppError(ErrorCode.VALIDATION, '填报数据招标编号与当前招标原文不一致', status_code=422)
    result = service.save_preview(user.id, invitationId, merged)
    return ok(result)


class ConfirmIn(BaseModel):
    invitationId: str
    choices: dict[str, str] = Field(default_factory=dict)
    acknowledged: bool = False


@router.get('/{token}')
async def get_preview(token: str, user: CurrentUser):
    try:
        data = service.load_import(token)
        if data['owner'] != int(user.id):
            raise ValueError('导入不属于当前用户')
        return ok(data)
    except (ValueError, OSError) as exc:
        raise AppError(ErrorCode.VALIDATION, str(exc), status_code=422) from exc


@router.post('/{token}/confirm')
async def confirm(token: str, body: ConfirmIn, user: CurrentUser):
    if not body.acknowledged:
        raise AppError(ErrorCode.VALIDATION, '请先核对预览与警告并确认采用', status_code=422)
    try:
        result = service.confirm(token, user.id, body.invitationId, body.choices)
        total = sum((service.number(r['values'][9]) for r in result['rows'] if r['kind'] == 'quote'), service.Decimal(0))
        return ok({'id': result['id'], 'counts': result['counts'], 'testData': result['testData'],
                   'bidPriceYuan': str(total) if any(r['kind'] == 'quote' for r in result['rows']) else None})
    except (ValueError, OSError) as exc:
        raise AppError(ErrorCode.VALIDATION, str(exc), status_code=422) from exc
