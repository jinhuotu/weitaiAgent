"""可管理的系统提示词。空表时种子一条通用「智能助手」，不含行业限定。"""

from __future__ import annotations

import secrets
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from common.errors import AppError, ErrorCode
from db.models.prompt import Prompt

_DEFAULT_SEED_NAME = "智能助手"
_DEFAULT_SEED_CONTENT = """你是智能助手，用简洁准确的中文回答用户问题。

【知识库使用规则】
1. 当消息中附有「知识库参考片段」时：优先依据相关片段作答；关键事实、数据、步骤须能在片段中找到依据，并标注来源（如「依据参考片段 #1」）。
2. 多条片段中只有部分相关时：只采用相关内容，不要把无关片段硬凑进答案。
3. 片段未覆盖或提示「未检索到匹配片段」时：先说明知识库未命中，再给通用经验建议，并标注「⚠️ 该结论非来自知识库」；不得假装引用了知识库。
4. 当提示「本次未启用知识库」时：禁止声称引用了企业知识库，仅基于通用经验与对话上下文作答。

【回答风格】
- 结构化中文：核心结论 → 可执行要点 → 依据（知识库编号或经验标注）。
- 表述专业、简洁；不确定时说明不确定，不要臆造数值或条文。"""


def short_id(n: int = 12) -> str:
    return secrets.token_hex((n + 1) // 2)[:n]


def to_item(row: Prompt, *, include_content: bool = True) -> dict[str, Any]:
    item: dict[str, Any] = {
        "id": row.public_id,
        "name": row.name,
        "remark": row.remark,
        "enabled": bool(row.enabled),
        "createdAt": int(row.created_at.timestamp() * 1000) if row.created_at else 0,
        "updatedAt": int(row.updated_at.timestamp() * 1000) if row.updated_at else 0,
    }
    if include_content:
        item["content"] = row.content
    return item


async def ensure_seed_prompt(db: AsyncSession) -> None:
    """空表时写入一条历史默认提示词，便于管理与对话选择。"""
    result = await db.execute(select(Prompt.id).limit(1))
    if result.scalar_one_or_none() is not None:
        return
    db.add(
        Prompt(
            public_id=short_id(12),
            name=_DEFAULT_SEED_NAME,
            content=_DEFAULT_SEED_CONTENT,
            remark="系统种子：通用对话提示词",
            enabled=True,
        )
    )
    await db.commit()


async def list_configs(db: AsyncSession) -> list[dict[str, Any]]:
    await ensure_seed_prompt(db)
    result = await db.execute(select(Prompt).order_by(Prompt.updated_at.desc()))
    return [to_item(r) for r in result.scalars().all()]


async def list_options(db: AsyncSession) -> list[dict[str, Any]]:
    """对话页可选：仅启用项，不含全文（减少载荷）。"""
    await ensure_seed_prompt(db)
    result = await db.execute(
        select(Prompt)
        .where(Prompt.enabled.is_(True))
        .order_by(Prompt.updated_at.desc())
    )
    return [to_item(r, include_content=False) for r in result.scalars().all()]


async def get_by_public_id(db: AsyncSession, public_id: str) -> Prompt:
    result = await db.execute(select(Prompt).where(Prompt.public_id == public_id))
    row = result.scalar_one_or_none()
    if row is None:
        raise AppError(ErrorCode.NOT_FOUND, "prompt not found", status_code=404)
    return row


async def get_enabled_content(db: AsyncSession, public_id: str) -> str:
    row = await get_by_public_id(db, public_id)
    if not row.enabled:
        raise AppError(ErrorCode.VALIDATION, "prompt is disabled", status_code=422)
    content = (row.content or "").strip()
    if not content:
        raise AppError(ErrorCode.VALIDATION, "prompt content is empty", status_code=422)
    return content


async def create_config(
    db: AsyncSession,
    *,
    name: str,
    content: str,
    remark: str | None = None,
    enabled: bool = True,
    created_by: int | None = None,
) -> dict[str, Any]:
    if not name.strip() or not content.strip():
        raise AppError(ErrorCode.VALIDATION, "name/content required", status_code=422)
    row = Prompt(
        public_id=short_id(12),
        name=name.strip()[:128],
        content=content.strip(),
        remark=(remark.strip()[:512] if remark and remark.strip() else None),
        enabled=bool(enabled),
        created_by=created_by,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return to_item(row)


async def update_config(
    db: AsyncSession,
    *,
    public_id: str,
    name: str | None = None,
    content: str | None = None,
    remark: str | None = None,
    enabled: bool | None = None,
) -> dict[str, Any]:
    row = await get_by_public_id(db, public_id)
    if name is not None:
        if not name.strip():
            raise AppError(ErrorCode.VALIDATION, "name required", status_code=422)
        row.name = name.strip()[:128]
    if content is not None:
        if not content.strip():
            raise AppError(ErrorCode.VALIDATION, "content required", status_code=422)
        row.content = content.strip()
    if remark is not None:
        row.remark = remark.strip()[:512] if remark.strip() else None
    if enabled is not None:
        row.enabled = bool(enabled)
    await db.commit()
    await db.refresh(row)
    return to_item(row)


async def delete_config(db: AsyncSession, *, public_id: str) -> None:
    row = await get_by_public_id(db, public_id)
    await db.delete(row)
    await db.commit()
