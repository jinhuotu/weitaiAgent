"""拼装对话 system：页面提示词为基座，这里只注入本轮检索结果和场景标记。"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from api.services.knowledge.access import TENDER_LIB_PUBLIC_ID

# 本轮事实标记；怎么回答写在「提示词管理」里的智能助手正文。
KB_MISS_MARK = "【知识库检索结果】未检索到匹配片段。"
KB_OFF_MARK = "【本次未启用知识库】"
VISION_HINT = "【本次附带用户上传图片】"
KB_IMAGE_HINT = "【本次附带资料库原件】"
TENDER_LIB_HINT = "【本次命中投标资料库原件】"
VIDEO_HINT = "【本次含视频转写片段】"


def uses_tender_lib(
    chunks: list[dict[str, Any]],
    kb_ids: list[str] | None = None,
) -> bool:
    _ = kb_ids
    return any(
        str(c.get("kb_id") or c.get("kbId") or "").strip() == TENDER_LIB_PUBLIC_ID
        for c in chunks
    )


def _has_video_chunks(chunks: list[dict[str, Any]]) -> bool:
    for c in chunks:
        kind = str(c.get("preview_kind") or "").strip().lower()
        ft = str(c.get("file_type") or "").strip().lower().lstrip(".")
        if kind == "video" or ft in {"mp4", "webm"}:
            return True
        if c.get("startMs") is not None or c.get("endMs") is not None:
            return True
    return False


def _fmt_clock_ms(ms: Any) -> str:
    try:
        n = int(ms)
    except (TypeError, ValueError):
        return ""
    if n < 0:
        n = 0
    sec = n // 1000
    return f"{sec // 60}:{sec % 60:02d}"


def _chunk_time_label(c: dict[str, Any]) -> str:
    start = _fmt_clock_ms(c.get("startMs"))
    end = _fmt_clock_ms(c.get("endMs"))
    if start and end:
        return f" 时段={start}-{end}"
    if start:
        return f" 起点={start}"
    return ""


async def build_system_prompt(
    db: AsyncSession,
    chunks: list[dict[str, Any]],
    *,
    base_prompt: str | None = None,
    use_knowledge: bool = True,
    has_images: bool = False,
    has_kb_images: bool = False,
    kb_ids: list[str] | None = None,
) -> str | None:
    _ = db
    base = (base_prompt or "").strip()
    vision = VISION_HINT if has_images else ""
    kb_img = KB_IMAGE_HINT if has_kb_images else ""
    tender = (
        TENDER_LIB_HINT
        if use_knowledge and chunks and has_kb_images and uses_tender_lib(chunks, kb_ids)
        else ""
    )
    video = VIDEO_HINT if use_knowledge and chunks and _has_video_chunks(chunks) else ""

    def _join(*parts: str) -> str | None:
        text = "\n\n".join(p for p in parts if p).strip()
        return text or None

    if not use_knowledge:
        off = KB_OFF_MARK if base else ""
        return _join(base, off, vision, kb_img)

    if not chunks:
        return _join(base, KB_MISS_MARK, vision, kb_img)

    refs: list[str] = []
    for i, c in enumerate(chunks[:5], start=1):
        score = float(c.get("score") or 0.0)
        content = str(c.get("content") or "").strip()
        name = str(c.get("name") or "").strip() or "未命名资料"
        chunk_index = c.get("chunk_index")
        idx_part = f" 块序={chunk_index}" if chunk_index is not None else ""
        kind = str(c.get("preview_kind") or "").strip().lower()
        type_part = " 类型=视频转写" if kind == "video" else ""
        time_part = _chunk_time_label(c)
        refs.append(
            f"[#{i} 资料={name}{idx_part}{type_part}{time_part} 相似度={score:.3f}]\n{content}"
        )
    joined = "\n\n---\n\n".join(refs)
    knowledge = f"【知识库参考片段（按相似度排序）】\n{joined}"
    return _join(base, knowledge, tender, video, vision, kb_img)
