"""Generic fallback pledges for an otherwise blank commitment subsection."""
from __future__ import annotations

from typing import Iterable


def generate_other_commitments(brief, existing_titles: Iterable[str] = ()) -> list[str]:
    existing = " ".join(existing_titles)
    facts = [str(value).strip() for value in (
        getattr(brief, "projectName", None), getattr(brief, "bidContent", None),
        getattr(brief, "deliveryDays", None), getattr(brief, "quality", None),
    ) if value not in (None, "", 0, 0.0)]
    context = "；".join(facts)
    candidates = [
        "我方承诺遵守合同约定和招标人现场管理要求，服从合理的工作协调安排。",
        "我方承诺不转包、不违法分包，并保证承接事项由我方依约组织实施。",
        "我方承诺配合审计、监督检查和资料核验，按要求提供真实、完整、可追溯的记录。",
        "我方承诺在阶段完成后及时整理竣工资料，按要求完成移交、归档和问题闭环。",
        "我方承诺对重大事项及时报告，并按既定应急程序组织响应、处置和复盘。",
        "我方承诺对履约过程中接触的资料承担保密义务，尊重并保护相关知识产权。",
        "我方承诺依法保障工资支付，按合同约定落实劳动用工管理和相关权益保障。",
        "我方承诺在质量责任期内外持续配合服务，对提出的事项及时响应并跟踪闭环。",
        "我方承诺本承诺书作为投标文件组成部分，与我方其他承诺具有同等约束效力。",
    ]
    if context:
        candidates[0] = f"我方承诺结合{context}遵守合同约定和现场管理要求，服从合理的工作协调安排。"
    # Existing subsection titles are used only to avoid repeating their subjects.
    return [text for text in candidates if not any(word and word in existing for word in (text[4:12],))]
