"""按模型管理表构建 LLM / Embedding 客户端。不回落到 .env。"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from api.services.ai.llm import LLMClient
from api.services.knowledge.embeddings import EmbeddingClient
from api.services.models import configs as model_configs
from common.errors import AppError, ErrorCode

_IMAGE_GEN_MARKERS = (
    "wan2.",
    "wanx",
    "image-pro",
    "dall-e",
    "dalle",
    "flux",
    "stable-diffusion",
    "sdxl",
    "imagen",
    "kolors",
    "ideogram",
)


def is_image_generation_model(model_name: str) -> bool:
    key = (model_name or "").strip().lower()
    return any(m in key for m in _IMAGE_GEN_MARKERS)


def _client_from_cfg(cfg: Any, mode: str) -> LLMClient:
    return LLMClient(
        api_base=cfg.api_base,
        api_key=cfg.api_key,
        model=cfg.model_name,
        temperature=cfg.temperature,
        timeout_seconds=cfg.timeout_seconds,
        mode=mode,
    )


async def resolve_chat_model_config(
    db: AsyncSession,
    mode: str = "fast",
    *,
    model_id: str | None = None,
    require_vision: bool = False,
    allow_image_gen: bool = False,
):
    """选对话模型：跳过文生图；需要看图时优先 multimodal_vision。"""
    ordered: list = []
    if model_id and str(model_id).strip():
        try:
            ordered.append(await model_configs.get_by_public_id(db, str(model_id).strip()))
        except AppError:
            pass
    active = await model_configs.resolve_active_llm(db, mode)
    if active is not None:
        ordered.append(active)
    if require_vision:
        vis = await model_configs.resolve_first_enabled_llm(db, model_type="multimodal_vision")
        if vis is not None:
            ordered.append(vis)
    first = await model_configs.resolve_first_enabled_llm(db)
    if first is not None:
        ordered.append(first)

    def usable(cfg) -> bool:
        if cfg is None or not cfg.enabled or cfg.kind != "llm":
            return False
        if not allow_image_gen and is_image_generation_model(cfg.model_name):
            return False
        if require_vision:
            mt = model_configs.normalize_model_type(cfg.kind, getattr(cfg, "model_type", None))
            if mt != "multimodal_vision":
                return False
        return True

    for cfg in ordered:
        if usable(cfg):
            return cfg
    if require_vision:
        for cfg in ordered:
            if (
                cfg is not None
                and cfg.enabled
                and cfg.kind == "llm"
                and not is_image_generation_model(cfg.model_name)
            ):
                return cfg
    raise AppError(
        ErrorCode.VALIDATION,
        "没有可用的对话模型。文生图模型（如 wan2.7-image-pro）不能用于读图或生成布置 JSON，"
        "请在「模型管理」启用 Qwen 等多模态视觉对话模型。",
        status_code=422,
    )


async def build_llm_client(
    db: AsyncSession,
    mode: str = "fast",
    *,
    model_id: str | None = None,
    require_vision: bool = False,
    allow_image_gen: bool = False,
) -> LLMClient:
    cfg = await resolve_chat_model_config(
        db,
        mode,
        model_id=model_id,
        require_vision=require_vision,
        allow_image_gen=allow_image_gen,
    )
    return _client_from_cfg(cfg, mode)


async def build_llm_client_by_id(
    db: AsyncSession,
    public_id: str,
    *,
    prefer_model_type: str | None = None,
    mode: str = "fast",
) -> LLMClient:
    cfg = await model_configs.get_by_public_id(db, public_id)
    if not cfg.enabled:
        raise AppError(ErrorCode.VALIDATION, "所选模型未启用", status_code=422)
    if cfg.kind != "llm":
        raise AppError(ErrorCode.VALIDATION, "所选配置不是对话/多模态模型", status_code=422)
    model_type = model_configs.normalize_model_type(
        cfg.kind, getattr(cfg, "model_type", None)
    )
    if prefer_model_type and model_type != prefer_model_type:
        raise AppError(
            ErrorCode.VALIDATION,
            f"请选择类型为「{prefer_model_type}」的模型（当前为 {model_type}）",
            status_code=422,
        )
    return LLMClient(
        api_base=cfg.api_base,
        api_key=cfg.api_key,
        model=cfg.model_name,
        temperature=cfg.temperature,
        timeout_seconds=cfg.timeout_seconds,
        mode=mode,
    )


async def build_embedding_client(db: AsyncSession) -> EmbeddingClient:
    cfg = await model_configs.resolve_active_embedding(db)
    if cfg is None:
        raise AppError(
            ErrorCode.INTERNAL,
            "Embedding not configured: 请在「模型管理」中新增 Embedding 模型并勾选「用于知识库」",
            status_code=503,
        )
    return EmbeddingClient(
        api_base=cfg.api_base,
        api_key=cfg.api_key,
        model=cfg.model_name,
        dim=int(cfg.embedding_dim or 1536),
    )
