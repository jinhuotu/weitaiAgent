"""Deterministic checks for structured cable scoring criteria against a DOCX."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import re
from typing import Any

from .scoring import ScoreCondition, ScoreCriterion


@dataclass(frozen=True)
class CriterionCheck:
    criterion_id: str
    status: str  # conforming / partial / nonconforming / indeterminate
    reason: str
    max_score: float | None
    matched_conditions: int
    total_conditions: int
    locations: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_score_report(criteria: list[ScoreCriterion], checks: list[CriterionCheck]) -> dict[str, Any]:
    """Build a stable UI/API payload without changing generation behavior."""
    by_id = {item.criterion_id: item for item in criteria}
    rows = []
    for check in checks:
        criterion = by_id.get(check.criterion_id)
        rows.append({
            **check.to_dict(),
            "category": criterion.category if criterion else "",
            "requirement": criterion.requirement if criterion else "",
            "sourceFile": criterion.source_file if criterion else "",
            "sourcePage": criterion.source_page if criterion else "",
            "evidenceRequired": criterion.evidence_required if criterion else False,
        })
    total = len(rows)
    conforming = sum(row["status"] == "conforming" for row in rows)
    partial = sum(row["status"] == "partial" for row in rows)
    nonconforming = sum(row["status"] == "nonconforming" for row in rows)
    indeterminate = sum(row["status"] == "indeterminate" for row in rows)
    available = sum(float(row["max_score"] or 0) for row in rows if row["status"] == "conforming")
    maximum = sum(float(row["max_score"] or 0) for row in rows)
    return {
        "version": "cable-score-check/1",
        "summary": {
            "total": total,
            "conforming": conforming,
            "partial": partial,
            "nonconforming": nonconforming,
            "indeterminate": indeterminate,
            "estimatedScore": available,
            "maxScore": maximum,
        },
        "rows": rows,
    }


def extract_docx_text(path: str | Path) -> tuple[str, ...]:
    """Extract paragraphs and table rows in document order with stable labels."""
    from docx import Document

    document = Document(str(path))
    chunks: list[str] = []
    for index, paragraph in enumerate(document.paragraphs, start=1):
        text = re.sub(r"\s+", " ", paragraph.text or "").strip()
        if text:
            chunks.append(f"段落{index}: {text}")
    for table_index, table in enumerate(document.tables, start=1):
        for row_index, row in enumerate(table.rows, start=1):
            text = " | ".join(re.sub(r"\s+", " ", cell.text or "").strip() for cell in row.cells)
            if text.strip():
                chunks.append(f"表{table_index}行{row_index}: {text}")
    return tuple(chunks)


def _condition_match(condition: ScoreCondition, text: str) -> bool | None:
    expected = "" if condition.expected is None else str(condition.expected).strip()
    if condition.operator in {"text", "eq", "enum"}:
        return bool(expected) and expected in text
    if condition.operator == "exists":
        return bool(text)
    if condition.operator in {"ge", "le"}:
        if not expected:
            return None
        numbers = re.findall(r"[-+]?\d+(?:\.\d+)?", text)
        if not numbers:
            return None
        actual = float(numbers[-1])
        target = float(expected)
        return actual >= target if condition.operator == "ge" else actual <= target
    if condition.operator == "range":
        bounds = re.findall(r"[-+]?\d+(?:\.\d+)?", expected)
        actuals = re.findall(r"[-+]?\d+(?:\.\d+)?", text)
        if len(bounds) < 2 or not actuals:
            return None
        return float(bounds[0]) <= float(actuals[-1]) <= float(bounds[1])
    return None


def check_criteria(criteria: list[ScoreCriterion], chunks: tuple[str, ...]) -> list[CriterionCheck]:
    results: list[CriterionCheck] = []
    for criterion in criteria:
        haystack = "\n".join(chunks)
        locations: list[str] = []
        for chunk in chunks:
            if criterion.requirement and criterion.requirement[:12] in chunk:
                locations.append(chunk.split(":", 1)[0])
        if not criterion.conditions:
            present = bool(criterion.requirement and criterion.requirement[:12] in haystack)
            status = "conforming" if present else "indeterminate"
            reason = "评分要求在生成稿中找到" if present else "未找到评分要求原文，需人工核验"
            results.append(CriterionCheck(criterion.criterion_id, status, reason, criterion.max_score, int(present), 0, tuple(locations)))
            continue
        matched = 0
        unknown = 0
        for condition in criterion.conditions:
            condition_text = "\n".join(chunk for chunk in chunks if condition.field in chunk)
            outcome = _condition_match(condition, condition_text)
            if outcome is True:
                matched += 1
            elif outcome is None:
                unknown += 1
        total = len(criterion.conditions)
        if matched == total:
            status, reason = "conforming", "全部评分条件已匹配"
        elif matched:
            status, reason = "partial", f"已匹配 {matched}/{total} 个评分条件"
        elif unknown == total:
            status, reason = "indeterminate", "生成稿缺少可判定的结构化证据"
        else:
            status, reason = "nonconforming", "评分条件未满足"
        results.append(CriterionCheck(criterion.criterion_id, status, reason, criterion.max_score, matched, total, tuple(locations)))
    return results


def check_docx(path: str | Path, criteria: list[ScoreCriterion]) -> list[CriterionCheck]:
    return check_criteria(criteria, extract_docx_text(path))

