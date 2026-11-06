"""Cable tender scoring criteria normalization.

This module is deliberately independent from the legacy tender services.  It
turns extracted scoring rows into stable, field-addressable criteria that a
later document checker can evaluate without relying on row numbers or prose
matching alone.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class ScoreCondition:
    field: str
    operator: str = "text"
    expected: Any = None
    unit: str = ""
    qualifier: str = ""
    required: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ScoreCriterion:
    criterion_id: str
    category: str
    requirement: str
    max_score: float | None = None
    source_file: str = ""
    source_page: str = ""
    evidence_required: bool = False
    conditions: tuple[ScoreCondition, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["conditions"] = [item.to_dict() for item in self.conditions]
        return result


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"评分值必须为数字：{value!r}") from exc


def _condition(raw: dict[str, Any]) -> ScoreCondition:
    field_name = str(raw.get("field") or raw.get("name") or "").strip()
    if not field_name:
        raise ValueError("评分条件缺少 field")
    operator = str(raw.get("operator") or "text").strip().lower()
    allowed = {"eq", "ge", "le", "range", "enum", "text", "exists"}
    if operator not in allowed:
        raise ValueError(f"不支持的评分条件算子：{operator}")
    return ScoreCondition(
        field=field_name,
        operator=operator,
        expected=raw.get("expected", raw.get("value")),
        unit=str(raw.get("unit") or "").strip(),
        qualifier=str(raw.get("qualifier") or "").strip(),
        required=bool(raw.get("required", True)),
    )


def normalize_scoring_rows(rows: list[dict[str, Any]]) -> list[ScoreCriterion]:
    """Normalize manually reviewed extraction rows into deterministic criteria.

    Input is intentionally a plain JSON-compatible structure so Excel/Word
    importers can be added later without coupling this contract to a parser.
    """
    result: list[ScoreCriterion] = []
    seen: set[str] = set()
    for index, row in enumerate(rows, start=1):
        criterion_id = str(row.get("criterion_id") or row.get("id") or f"score-{index:03d}").strip()
        if not criterion_id or criterion_id in seen:
            raise ValueError(f"评分项 ID 缺失或重复：{criterion_id!r}")
        requirement = str(row.get("requirement") or row.get("text") or "").strip()
        if not requirement:
            raise ValueError(f"评分项 {criterion_id} 缺少 requirement")
        conditions = tuple(_condition(item) for item in (row.get("conditions") or []))
        result.append(ScoreCriterion(
            criterion_id=criterion_id,
            category=str(row.get("category") or "技术评分").strip(),
            requirement=requirement,
            max_score=_number(row.get("max_score", row.get("scoreWeight"))),
            source_file=str(row.get("source_file") or "").strip(),
            source_page=str(row.get("source_page") or "").strip(),
            evidence_required=bool(row.get("evidence_required", False)),
            conditions=conditions,
        ))
        seen.add(criterion_id)
    return result

