from pathlib import Path
from tempfile import NamedTemporaryFile
import json
from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from api.deps import CurrentUser
from api.services.cablepkg.structured_import import dry_run
from api.services.cablepkg.structured_apply import apply_offers, apply_prices
from api.services.cablepkg.scoring import normalize_scoring_rows
from api.services.cablepkg.scorecheck import build_score_report, check_docx

router = APIRouter(prefix='/cablepkg/structured', tags=['cablepkg-structured'])

score_router = APIRouter(prefix='/cablepkg/score-check', tags=['cablepkg-score-check'])

@router.post('/dry-run')
async def structured_dry_run(user: CurrentUser, file: UploadFile = File(...), targets: str = Form('{}'), price_targets: str = Form('{}')):
    if Path(file.filename or '').suffix.lower() != '.xlsx': raise HTTPException(422, '仅支持 XLSX')
    import json
    data = await file.read()
    if len(data) > 50 * 1024 * 1024: raise HTTPException(413, '文件过大')
    with NamedTemporaryFile(suffix='.xlsx', delete=True) as f:
        f.write(data); f.flush()
        return dry_run(f.name, targets=json.loads(targets), price_targets=json.loads(price_targets))

@router.post('/apply')
async def structured_apply(user: CurrentUser, payload: dict):
    session = payload.get('session')
    if not isinstance(session, dict): raise HTTPException(422, '缺少输入会话')
    result = session
    actor = str(payload.get('actor') or '').strip(); basis = str(payload.get('basis') or '').strip()
    if not actor or not basis: raise HTTPException(422, '确认人和依据必填')
    result = apply_offers(result, payload.get('offers', []), actor=actor, basis=basis)
    result = apply_prices(result, payload.get('prices', []), actor=actor, basis=basis)
    return {'session': result, 'components': payload.get('components', []), 'deviations': payload.get('deviations', [])}


@score_router.post('')
async def score_check_docx(
    user: CurrentUser,
    file: UploadFile = File(..., description='生成后的投标 Word（.docx）'),
    criteria: str = Form('[]', description='结构化评分项 JSON 数组'),
):
    """Check a generated cable bid without touching the legacy tender flow."""
    if Path(file.filename or '').suffix.lower() != '.docx':
        raise HTTPException(422, '仅支持 DOCX')
    try:
        rows = json.loads(criteria)
        if not isinstance(rows, list):
            raise ValueError('criteria 必须是数组')
        normalized = normalize_scoring_rows(rows)
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(422, f'评分标准格式错误：{exc}') from exc
    data = await file.read()
    if not data:
        raise HTTPException(422, 'Word 文件为空')
    if len(data) > 50 * 1024 * 1024:
        raise HTTPException(413, '文件过大')
    with NamedTemporaryFile(suffix='.docx', delete=True) as temp:
        temp.write(data)
        temp.flush()
        checks = check_docx(temp.name, normalized)
    return {'report': build_score_report(normalized, checks)}
