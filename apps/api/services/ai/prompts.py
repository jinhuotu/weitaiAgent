"""拼装对话 system 提示：可选基座提示词 + 知识库命中片段。

未迁：数据治理 Excel 参考块、Qdrant 长期记忆。
未选提示词且未开知识库时返回 None，调用方不向 LLM 传 system。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession


async def build_system_prompt(
    db: AsyncSession,
    chunks: list[dict[str, Any]],
    *,
    base_prompt: str | None = None,
    use_knowledge: bool = True,
) -> str | None:
    _ = db
    base = (base_prompt or "").strip()

    if not use_knowledge:
        return base or None

    if not chunks:
        if not base:
            return None
        text = (
            f"{base}\n\n【知识库参考片段】\n"
            "（已开启知识库，但本次未检索到足够相关的片段。"
            "请先明确告知「知识库暂未命中相关内容」，再基于通用经验作答，"
            "并标注「⚠️ 该结论非来自知识库」。）"
        ).strip()
        return text or None

    refs: list[str] = []
    for i, c in enumerate(chunks[:5], start=1):
        score = float(c.get("score") or 0.0)
        content = str(c.get("content") or "").strip()
        name = str(c.get("name") or "").strip() or "未命名资料"
        chunk_index = c.get("chunk_index")
        idx_part = f" 块序={chunk_index}" if chunk_index is not None else ""
        refs.append(f"[#{i} 资料={name}{idx_part} 相似度={score:.3f}]\n{content}")
    joined = "\n\n---\n\n".join(refs)
    text = (
        f"{base}\n\n"
        f"【知识库参考片段（按相似度排序）】\n{joined}\n\n"
        "【本轮引用要求】\n"
        "优先使用以上片段回答当前问题；引用时标明片段编号（如参考片段 #1）；"
        "不要编造片段中不存在的条款、数值或步骤；无关片段请忽略。"
    ).strip()
    return text or None
