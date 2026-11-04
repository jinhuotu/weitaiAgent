"""Read-only post-generation validity checks. Never called by document builders."""
import logging
from pathlib import Path
from api.services.knowledge.expiry import expiry_fields
from common.config import get_settings

logger = logging.getLogger(__name__)


def collect_expiry_warnings(report, media, documents, *, today=None, project_end=None):
    """Intersect matched keys with the final, already scoped file selection."""
    indexed = {}
    for doc, path in documents:
        indexed.setdefault(Path(path).resolve(), []).append(doc)
    out, seen = [], set()
    for item in (report or {}).get('matched', []):
        if int(item.get('fileCount') or 0) <= 0:
            continue
        key = item.get('key')
        for path in media.get(key, []):
            path = Path(path)
            if not path.is_file():
                continue
            for doc in indexed.get(path.resolve(), []):
                identity = (key, doc.public_id)
                if identity in seen:
                    continue
                seen.add(identity)
                info = expiry_fields(doc, today=today)
                state, level = info['expire_status'], info['expire_level']
                advice = ''
                if state == 'expired':
                    advice = '请更换有效证件或办理延续'
                elif state == 'expiring':
                    advice = '请及时办理延续，核对本次投标有效性'
                elif state == 'unknown' and get_settings().expire_unknown_warn:
                    level, advice = 'info', '建议在资料库维护到期日期或标记长期'
                elif state == 'normal' and project_end and doc.expire_at <= project_end:
                    state, level, advice = 'project_expiry', 'info', '预计在项目执行期内到期，建议提前办理延续'
                if advice:
                    out.append(dict(type='material_expiry', key=key, material_name=item.get('title') or key,
                                    document_id=doc.public_id, document_name=doc.name,
                                    expire_at=info['expire_at'], expire_status=state,
                                    remaining_days=info['expire_days'], level=level, advice=advice))
    return out


async def generation_expiry_warnings(db, report, media):
    """Use resolved file identities, without resolving or modifying matches again."""
    try:
        from api.services.tenders.library_kb import (
            TENDER_LIB_PUBLIC_ID, get_base_by_public_id, _list_docs, _doc_file_path,
        )
        base = await get_base_by_public_id(db, TENDER_LIB_PUBLIC_ID)
        docs = await _list_docs(db, base.id)
        documents = [(doc, path) for doc in docs if (path := _doc_file_path(doc))]
        return collect_expiry_warnings(report, media, documents)
    except Exception:
        logger.exception('Post-generation expiry check unavailable; generation remains successful')
        return []
