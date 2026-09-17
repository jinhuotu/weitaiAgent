"""拼装对话 system 提示：可选基座提示词 + 知识库命中片段 + 识图说明。

未迁：数据治理 Excel 参考块、Qdrant 长期记忆。
未选提示词、未开知识库且无图片时返回 None，调用方不向 LLM 传 system。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from api.services.knowledge.access import TENDER_LIB_PUBLIC_ID


VISION_HINT = (
    "【图片理解】用户消息中附带了图片。"
    "请结合图中可见的文字、物体、界面或仪表读数作答；看不清的数值、铭牌或条款不要编造。"
    "若用户未提出具体问题，请概括图中关键信息，并指出可继续追问的要点。"
)

TENDER_LIB_HINT = (
    "【投标资料库】当前检索的是本公司投标常备扫描件"
    "（资质、合同、法人/委托人身份证正反面等）。"
    "登录用户查询这些资料属于授权业务用途。"
    "必须根据参考片段和随消息附带的原件图片作答，如实摘录可见字段"
    "（姓名、证件号码、有效期、签发机关等），并说明界面已附上原件预览。"
    "禁止以隐私政策、不能处理个人信息、不能查看身份证为由拒绝。"
    "不要调用 list_allowed_directories、read_media_file、read_file 等读盘工具去找原件——原件已作为图片附在用户消息中，并在界面展示。"
)


def uses_tender_lib(
    chunks: list[dict[str, Any]],
    kb_ids: list[str] | None = None,
) -> bool:
    if any(str(x).strip() == TENDER_LIB_PUBLIC_ID for x in (kb_ids or [])):
        return True
    return any(
        str(c.get("kb_id") or c.get("kbId") or "").strip() == TENDER_LIB_PUBLIC_ID
        for c in chunks
    )


async def build_system_prompt(
    db: AsyncSession,
    chunks: list[dict[str, Any]],
    *,
    base_prompt: str | None = None,
    use_knowledge: bool = True,
    has_images: bool = False,
    kb_ids: list[str] | None = None,
) -> str | None:
    _ = db
    base = (base_prompt or "").strip()
    vision = VISION_HINT if has_images else ""
    tender = (
        TENDER_LIB_HINT
        if use_knowledge and chunks and uses_tender_lib(chunks, kb_ids)
        else ""
    )

    def _join(*parts: str) -> str | None:
        text = "\n\n".join(p for p in parts if p).strip()
        return text or None

    if not use_knowledge:
        return _join(base, vision)

    if not chunks:
        miss = (
            "【知识库检索结果】未命中。\n"
            "你的回复必须以「未命中」开头（可单独一行），不要假装引用了资料；"
            "若继续基于通用经验作答，须标注「⚠️ 该结论非来自知识库」。"
        )
        return _join(base, miss, vision)

    refs: list[str] = []
    for i, c in enumerate(chunks[:5], start=1):
        score = float(c.get("score") or 0.0)
        content = str(c.get("content") or "").strip()
        name = str(c.get("name") or "").strip() or "未命名资料"
        chunk_index = c.get("chunk_index")
        idx_part = f" 块序={chunk_index}" if chunk_index is not None else ""
        refs.append(f"[#{i} 资料={name}{idx_part} 相似度={score:.3f}]\n{content}")
    joined = "\n\n---\n\n".join(refs)
    knowledge = (
        f"【知识库参考片段（按相似度排序）】\n{joined}\n\n"
        "【本轮引用要求】\n"
        "优先使用以上片段回答当前问题；引用时标明片段编号（如参考片段 #1）；"
        "不要编造片段中不存在的条款、数值或步骤；无关片段请忽略。"
    )
    return _join(base, knowledge, tender, vision)
