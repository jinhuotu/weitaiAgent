"""文本切块：先按手册章节标题切开，再 RecursiveCharacterTextSplitter。"""

from __future__ import annotations

import re

from langchain_text_splitters import RecursiveCharacterTextSplitter

from common.config import get_settings

# 充电站布置案例卡：整篇入库，避免「1. 方案摘要」被切成碎片
_CASE_HEAD_RE = re.compile(
    r"(?m)^(?:#\s*)?充电站.*布置案例|^1\.\s*方案摘要|^5\.\s*生成时如何复用"
)

# 手册常见小节标题：8.3 厂区突发停电 / 第九章 安全操作禁令 / 6.2 冷却管控
_SECTION_HEAD_RE = re.compile(
    r"(?m)^(?:"
    r"第[一二三四五六七八九十百千零〇两\d]+[章节篇部]"
    r"|\d+(?:\.\d+){1,3}"
    r")[、.\s　].+$"
)


def is_layout_case_card(text: str) -> bool:
    raw = text or ""
    if "方案摘要" in raw and "生成时如何复用" in raw:
        return True
    if "规模配置" in raw and "布置模式" in raw and "parkingRows" in raw:
        return True
    hits = _CASE_HEAD_RE.findall(raw)
    return len(hits) >= 2


def summarize_layout_case_for_prompt(text: str, *, name: str = "") -> str:
    """检索进 LLM 时去掉规模数字，只留布置手法，避免整案照抄。"""
    raw = (text or "").strip()
    if not raw:
        return ""
    if not is_layout_case_card(raw):
        return raw[:1500]
    chunks: list[str] = []
    for title in ("布置模式", "生成时如何复用"):
        block = _section_named(raw, title)
        if block:
            chunks.append(block.strip())
    body = "\n".join(chunks) if chunks else raw[:800]
    body = re.sub(r"\d+\s*台\s*\d+\s*kVA", "箱变台数与容量以用户为准", body, flags=re.I)
    body = re.sub(r"\d+\s*[×xX]\s*\d+\s*kVA", "箱变容量以用户为准", body, flags=re.I)
    body = re.sub(r"\d+\s*台(?:直流桩|交流桩|充电桩)", "桩数以用户为准", body)
    body = re.sub(r"(?:直流桩|交流桩)\s*\d+\s*台", "桩数以用户为准", body)
    title = (name or "").strip() or "历史案例"
    return (
        f"案例「{title}」只借鉴车位朝向、沟截面、图例，"
        f"禁止照抄桩数、箱变台数/容量、场地尺寸、道路轮廓。\n{body[:1600]}"
    )


def _section_named(text: str, title: str) -> str:
    cre = re.compile(
        rf"(?ms)^(?:\d+\.\s*)?{re.escape(title)}\s*\n(.*?)(?=^\d+\.\s|\Z)"
    )
    m = cre.search(text)
    return m.group(0) if m else ""


_PAGE_OR_SHEET_RE = re.compile(r"(?m)^(?:\[第\d+页\]|##\s+\S+)")


def _normalize(text: str) -> str:
    cleaned = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.strip() for line in cleaned.split("\n")]
    return "\n".join(line for line in lines if line)


def _split_by_page_or_sheet(text: str) -> list[str]:
    """先按 PDF「第 N 页」或 Excel 工作表切开。"""
    matches = list(_PAGE_OR_SHEET_RE.finditer(text))
    if not matches:
        return [text]
    parts: list[str] = []
    if matches[0].start() > 0:
        head = text[: matches[0].start()].strip()
        if head:
            parts.append(head)
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        block = text[m.start() : end].strip()
        if block:
            parts.append(block)
    return parts or [text]


def _split_by_sections(text: str) -> list[str]:
    """按章节标题切开，标题保留在对应段落开头，避免「停电」标题与正文拆散。"""
    matches = list(_SECTION_HEAD_RE.finditer(text))
    if len(matches) < 2:
        return [text]

    parts: list[str] = []
    # 文首到第一个标题
    if matches[0].start() > 0:
        head = text[: matches[0].start()].strip()
        if head:
            parts.append(head)

    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        block = text[m.start() : end].strip()
        if block:
            parts.append(block)
    return parts or [text]


def _split_oversized(block: str, *, chunk_size: int, chunk_overlap: int) -> list[str]:
    if len(block) <= chunk_size:
        return [block]
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=["\n\n", "\n", "。", "！", "？", "；", " ", ""],
    )
    # 超长块：尽量把首行标题带到每个子块
    first_line, _, rest = block.partition("\n")
    title = first_line.strip() if _SECTION_HEAD_RE.match(first_line.strip()) else ""
    pieces = [c.strip() for c in splitter.split_text(block) if c.strip()]
    if not title:
        return pieces
    out: list[str] = []
    for p in pieces:
        if p.startswith(title):
            out.append(p)
        else:
            out.append(f"{title}\n{p}")
    return out


def split_text(text: str) -> list[str]:
    settings = get_settings()
    cleaned = _normalize(text)
    if not cleaned.strip():
        return []

    chunk_size = settings.kb_chunk_size
    chunk_overlap = settings.kb_chunk_overlap
    if is_layout_case_card(cleaned):
        if len(cleaned) <= max(chunk_size * 4, 4000):
            return [cleaned]
        return _split_oversized(cleaned, chunk_size=max(chunk_size, 2000), chunk_overlap=chunk_overlap)
    chunks: list[str] = []
    for page_block in _split_by_page_or_sheet(cleaned):
        for block in _split_by_sections(page_block):
            chunks.extend(
                _split_oversized(block, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
            )

    return chunks or [cleaned[:chunk_size]]
