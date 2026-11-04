"""报价产出目录，以及从任意知识库文档里找出原文件。"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from api.services.knowledge import access as kb_access
from api.services.knowledge.bases import PURPOSE_ASSET
from api.services.knowledge.ingest import list_documents, resolve_storage_path
from common.config import get_settings
from common.errors import AppError, ErrorCode
from db.models.user import User


def quotes_output_dir() -> Path:
    root = Path(get_settings().storage_root).expanduser().resolve()
    path = root / "quotes"
    path.mkdir(parents=True, exist_ok=True)
    return path


async def docs_for_quote_kb(
    db: AsyncSession,
    user: User,
    base_id: str,
) -> tuple[str, str, list[dict]]:
    pid = (base_id or "").strip()
    if not pid:
        raise AppError(ErrorCode.VALIDATION, "请选择知识库", status_code=422)
    base = await kb_access.require_base(db, user, pid, kb_access.PERM_USE)
    purpose = (getattr(base, "purpose", None) or "rag").strip().lower()
    if purpose == PURPOSE_ASSET or base.public_id == kb_access.TENDER_LIB_PUBLIC_ID:
        raise AppError(ErrorCode.VALIDATION, "请选择普通知识库，投标资料库不能用于对价", status_code=422)
    docs = await list_documents(db, base_public_id=base.public_id)
    return base.public_id, base.name, docs


def stored_paths(docs: list[dict]) -> list[Path]:
    out: list[Path] = []
    for d in docs:
        key = str(d.get("fileKey") or d.get("storagePath") or "").strip()
        if not key:
            continue
        try:
            path = resolve_storage_path(key)
        except AppError:
            continue
        if path.is_file():
            out.append(path)
    return out
