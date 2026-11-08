"""Extract clause facts from tender instructions and their schedules.

The parser deliberately discovers structure from text and table headers.  It
does not assume a fixed number of columns or a particular template title.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any


_CLAUSE_RE = re.compile(
    r"^\s*(\d+(?:[.．]\d+){1,3}|[（(][一二三四五六七八九十百0-9]+[）)]|[一二三四五六七八九十百]+、)\s*"
    r"([\u4e00-\u9fff（）()]{2,18})\s*[:：]?\s*(.{6,})"
)
_CLAUSE_CELL_RE = re.compile(r"^\s*(\d+(?:[.．]\d+){1,3})\s*[、.．]?")
_ORDINAL_RE = re.compile(r"^\s*[（(][一二三四五六七八九十百0-9]+[）)]\s*$")
_HEADER_LEFT = ("条款号", "序号", "编号")
_HEADER_RIGHT = ("条款名称", "条款", "项目", "内容")
_HEADER_VALUE = ("编列内容", "条款内容", "内容", "规定", "要求")
FACT_GROUPS = {
    "工程概况": ("工程概况", "项目概况", "工程内容", "建设内容", "项目简介", "工程说明"),
    "招标范围": ("招标范围", "投标范围", "承包范围", "工作内容", "采购范围", "服务范围"),
    "建设地点": ("建设地点", "项目地点", "工程地点", "实施地点", "服务地点"),
    "资金来源": ("资金来源", "资金构成", "投资来源", "资金渠道"),
    "\u8d28\u91cf\u6807\u51c6": ("\u8d28\u91cf\u6807\u51c6", "\u8d28\u91cf\u8981\u6c42", "\u8d28\u91cf\u76ee\u6807", "\u9a8c\u6536\u6807\u51c6"),
    "\u5b89\u5168\u8981\u6c42": ("\u5b89\u5168\u8981\u6c42", "\u5b89\u5168\u76ee\u6807", "\u5b89\u5168\u6587\u660e\u65bd\u5de5\u8981\u6c42"),
    "\u8ba1\u5212\u5de5\u671f": ("\u8ba1\u5212\u5de5\u671f", "\u5de5\u671f", "\u603b\u5de5\u671f", "\u5408\u540c\u5de5\u671f", "\u670d\u52a1\u671f\u9650", "\u4ea4\u8d27\u671f"),
}

_L2_RE = re.compile(
    r"^\s*\d+(?:[.．]\d+){1,3}\s*[、.．]?\s*"
    r"[\u4e00-\u9fff（）()]{2,18}\s*[:：]?\s*.{6,}"
)
_TRIPLE_NUMBER_RE = re.compile(r"^\s*\d+[.．]\d+[.．]\d+")
_CHAPTER_RE = re.compile(r"^第[一二三四五六七八九十0-9]+章")


def _block_parts(block: Any) -> tuple[str, list[list[str]]]:
    if isinstance(block, (tuple, list)) and len(block) >= 3:
        return _text(block[1]), [[_text(cell) for cell in row] for row in (block[2] or [])]
    return _text(block), []


def classify_book_form(invitation_text: str, blocks: Any) -> tuple[str, str]:
    """Classify a book from structural evidence, in first-hit order."""

    text = str(invitation_text or "")
    block_list = list(blocks or [])
    headings = [_block_parts(block)[0] for block in block_list]
    l0 = any("须知" in line and "前附表" in line for line in text.splitlines())
    l1 = any(_header_match(rows[0]) for block in block_list for _, rows in [_block_parts(block)] if rows)
    score_table = any(
        any(
            ("评分标准" in line or "评分因素" in line) and ("分值" in line or "分" in line)
            for line in [*heading_cells, *[cell for row in rows for cell in row]]
        )
        for block in block_list
        for heading, rows in [_block_parts(block)]
        for heading_cells in [[heading]]
    )
    if (l0 or l1) and score_table:
        return "structured", "L0/L1前附表或表头 + 评分标准分值表"

    l2_count = sum(bool(_L2_RE.match(line)) for line in text.splitlines())
    triple_count = sum(bool(_TRIPLE_NUMBER_RE.match(line)) for line in text.splitlines())
    if l2_count >= 5 or triple_count >= 3:
        return "prose", f"L2三段式{l2_count}条/三级编号{triple_count}处"

    paragraphs = [line.strip() for line in text.splitlines() if line.strip()]
    average = sum(len(line) for line in paragraphs) / max(1, len(paragraphs))
    table_count = sum(bool(rows) for block in block_list for _, rows in [_block_parts(block)])
    fragments = sum(
        len(line) >= 8 and not re.search(r"[，。；：:,.!?！？]", line) and len(set(line)) <= 4
        for line in paragraphs
    )
    if (table_count == 0 and average < 40) or fragments >= 20:
        return "scanned", "无结构表格、段落短碎或OCR等长无标点片段"
    return "scanned", "未满足结构化表格或正文条款条件"


def canonical_fact_name(name: str) -> str:
    value = _text(name)
    best = value
    best_score = 0.0
    for canonical, aliases in FACT_GROUPS.items():
        for alias in aliases:
            a = {alias[i:i + 2] for i in range(max(0, len(alias) - 1))}
            b = {value[i:i + 2] for i in range(max(0, len(value) - 1))}
            score = len(a & b) / max(1, len(a | b))
            if score >= 0.7 and score > best_score:
                best, best_score = canonical, score
    return best


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _normal_clause(value: str) -> str:
    return value.replace("．", ".").replace("、", ".").strip(" .")


def _header_match(cells: list[str]) -> bool:
    joined = "|".join(cells)
    return (
        any(token in joined for token in _HEADER_LEFT)
        and any(token in joined for token in _HEADER_RIGHT)
        and any(token in joined for token in _HEADER_VALUE)
    )


def _row_fact(cells: list[str], row_index: int, header: list[str] | None = None) -> tuple[str, str, str] | None:
    cells = [_text(cell) for cell in cells if _text(cell)]
    if len(cells) < 2:
        return None
    number_index = next((i for i, cell in enumerate(cells) if _CLAUSE_CELL_RE.match(cell)), None)
    if number_index is None:
        ordinal = next((cell for cell in cells if _ORDINAL_RE.match(cell)), "")
        number = ordinal or f"row-{row_index + 1}"
    else:
        number = _normal_clause(_CLAUSE_CELL_RE.match(cells[number_index]).group(1))

    remaining = [cell for i, cell in enumerate(cells) if i != number_index]
    if not remaining:
        return None
    # The final sufficiently long cell is the value in merged or irregular rows.
    value = max(remaining, key=len)
    name_candidates = [cell for cell in remaining if cell != value]
    name = min(name_candidates or remaining, key=len)
    if header:
        value_index = next((i for i, cell in enumerate(header) if any(token in cell for token in _HEADER_VALUE)), None)
        name_index = next(
            (
                i
                for i, cell in enumerate(header)
                if any(token in cell for token in _HEADER_RIGHT)
                and not any(token in cell for token in _HEADER_LEFT)
                and not any(token in cell for token in _HEADER_VALUE)
            ),
            None,
        )
        if value_index is not None and value_index < len(cells) and cells[value_index].strip():
            value = _text(cells[value_index])
        if name_index is not None and name_index < len(cells) and cells[name_index].strip():
            name = _text(cells[name_index])
    if name == value and len(remaining) > 1:
        name, value = sorted(remaining, key=len)[:2]
    if not value or not name:
        return None
    return number, name, value


def _paragraph_source(source: Any) -> tuple[list[str], list[Any]]:
    if hasattr(source, "paragraphs") and hasattr(source, "tables"):
        return [_text(p.text) for p in source.paragraphs], list(source.tables)
    if isinstance(source, (str, Path)):
        path = Path(source)
        if path.exists() and path.suffix.lower() == ".docx":
            from docx import Document

            doc = Document(str(path))
            return [_text(p.text) for p in doc.paragraphs], list(doc.tables)
        return [_text(line) for line in str(source).splitlines()], []
    return [_text(line) for line in str(source or "").splitlines()], []


def extract_tender_facts(source: Any) -> dict[str, Any]:
    """Return discovered facts, clause records and non-fatal notes."""

    started = time.perf_counter()
    paragraphs, tables = _paragraph_source(source)
    facts: dict[str, str] = {}
    clauses: dict[str, dict[str, str]] = {}
    notes: list[str] = []

    def put_fact(name: str, value: str) -> None:
        key = canonical_fact_name(name)
        if key in facts and facts[key] != value:
            key = name
            suffix = 2
            while key in facts:
                key = f"{name}#{suffix}"
                suffix += 1
        facts[key] = value

    has_l0 = any("须知" in line and "前附表" in line for line in paragraphs)
    for table in tables:
        rows = [[_text(cell.text) for cell in row.cells] for row in table.rows]
        header_at = next((i for i, cells in enumerate(rows) if _header_match(cells)), None)
        if header_at is None:
            continue
        for index, cells in enumerate(rows[header_at + 1 :], header_at + 1):
            item = _row_fact(cells, index, rows[header_at])
            if item is None:
                continue
            number, name, value = item
            clauses[number] = {"name": name, "value": value}
            put_fact(name, value)

    if not clauses:
        for line in paragraphs:
            match = _CLAUSE_RE.match(line)
            if not match:
                continue
            number = _normal_clause(match.group(1))
            name, value = match.group(2).strip(), match.group(3).strip()
            clauses[number] = {"name": name, "value": value}
            put_fact(name, value)

    if not clauses:
        notes.append("未定位到须知前附表、表头或正文三段式条款")
    if not has_l0 and tables and clauses:
        notes.append("未发现须知前附表标题，已按表头层提取")
    elapsed_ms = (time.perf_counter() - started) * 1000
    if elapsed_ms > 1500:
        notes.append(f"抽取耗时 {elapsed_ms:.1f}ms，超过1500ms")
    blocks = [(i, paragraph, []) for i, paragraph in enumerate(paragraphs)]
    blocks.extend(
        (len(paragraphs) + i, "", [[cell.text for cell in row.cells] for row in table.rows])
        for i, table in enumerate(tables)
    )
    form, evidence = classify_book_form("\n".join(paragraphs), blocks)
    notes.append(f"标书形态：{form}（依据：{evidence}）")
    result = {"facts": facts, "clauses": clauses, "notes": notes, "elapsed_ms": elapsed_ms, "bookForm": form}
    if form == "scanned":
        notes.append("已按纯文本兜底，需人工复核")
        if len(facts) < 3:
            result["blockNotice"] = "抽取结果不足，纯扫描件结构已丢失，需人工复核"
    return result


def parse_tender_facts(source: Any) -> dict[str, Any]:
    """Compatibility alias for callers that use a parser-style name."""

    return extract_tender_facts(source)

