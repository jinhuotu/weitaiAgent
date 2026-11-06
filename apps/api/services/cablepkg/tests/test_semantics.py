"""Independent annotated syntax cases and read-only seed regression reports."""

import json
from dataclasses import is_dataclass
from pathlib import Path

import pytest

from apps.api.services.cablepkg.model import EvidenceLevel, Fact, Requirement, Response
from apps.api.services.cablepkg.semantics import parse_requirement


@pytest.mark.parametrize(
    "raw,operator,value,condition",
    [
        ("大于等于0.9", "ge", 0.9, None),
        ("不小于4.5(10kV)", "ge", 4.5, "10kV"),
        ("≥ 5", "ge", 5, None),
        ("小于等于10", "le", 10, None),
        ("不大于0.5（对于3×240mm2截面以下）", "le", 0.5, "对于3×240mm2截面以下"),
        ("≤ -20", "le", -20, None),
        ("4-45（10kV）", "range", [4, 45], "10kV"),
        ("4～45", "range", [4, 45], None),
        ("-40--20", "range", [-40, -20], None),
        ("±25", "range", [-25, 25], None),
        ("10±2", "range", [8, 12], None),
        ("小于等于±25（PVC)", "le", 25, "PVC"),
        ("17.5（10kV)", "eq", 17.5, "10kV"),
        ("-40", "eq", -40, None),
        (".5", "eq", 0.5, None),
        ("铜、铝", "enum", ["铜", "铝"], None),
        ("PVC或PE", "enum", ["PVC", "PE"], None),
        ("PVC/PE", "enum", ["PVC", "PE"], None),
        ("不锈钢带（非磁性）", "text", "不锈钢带（非磁性）", "非磁性"),
        ("3×240", "text", "3×240", None),
        ("0.2g", "text", "0.2g", None),
        ("大于等于不涉及", "text", "大于等于不涉及", None),
        ("(投标人提供)", "bidder_supplied", None, "投标人提供"),
        ("小于等于（投标人提供）", "bidder_supplied", None, "投标人提供"),
        ("无", "na", None, None),
        ("", "empty", None, None),
        ("  \n ", "empty", None, None),
    ],
)
def test_syntax(raw, operator, value, condition):
    parsed = parse_requirement(raw)
    assert parsed.raw == raw
    assert (parsed.operator, parsed.value, parsed.unit_condition) == (operator, value, condition)
    assert parsed.bidder_supplied == (operator == "bidder_supplied")
    assert parsed.qualifier is None


@pytest.mark.parametrize(
    "raw,operator,value,condition,qualifier",
    [
        ("大于等于90%标称厚度", "ge", 90, None, "%标称厚度"),
        ("大于等于80%标称厚度（有铠装）", "ge", 80, "有铠装", "%标称厚度"),
        ("无", "na", None, None, None),
        ("不涉及", "na", None, None, None),
        ("—", "na", None, None, None),
        ("小于等于0.253（对应120mm2铝截面）", "le", 0.253, "对应120mm2铝截面", None),
        (">90%标称厚度", "ge", 90, None, "%标称厚度"),
        ("90%标称厚度", "eq", 90, None, "%标称厚度"),
        ("≤10%", "le", 10, None, "%"),
        ("-", "na", None, None, None),
    ],
)
def test_annotated_field_semantics(raw, operator, value, condition, qualifier):
    # Expectations are manually annotated, independent of the seed and parser.
    parsed = parse_requirement(raw)
    assert (parsed.operator, parsed.value, parsed.unit_condition, parsed.qualifier) == (
        operator, value, condition, qualifier,
    )
    assert parsed.to_dict()["qualifier"] == qualifier


def test_name_markers_and_serialization():
    requirement = Requirement(name=" ※绝缘△偏心度 ", seq="1.12", unit="%")
    assert requirement.name == "绝缘偏心度"
    assert requirement.is_key and requirement.needs_report
    assert requirement.to_dict()["seq"] == "1.12"
    assert requirement.to_dict()["unit"] == "%"
    assert Requirement(name="普通参数", is_key=True, needs_report=True).is_key
    for cls in (Requirement, Fact, Response):
        assert is_dataclass(cls)
        assert json.loads(json.dumps(cls().to_dict())) == cls().to_dict()
    assert [level.value for level in EvidenceLevel] == ["L0", "L1", "L2", "L3", "L4"]
    for level in EvidenceLevel:
        assert level.to_dict() == {"value": level.value}
        assert Fact(evidence_level=level).to_dict()["evidence_level"] == level.value
        assert Response(evidence_level=level).to_dict()["evidence_level"] == level.value


def _inference_reason(parsed):
    if parsed.bidder_supplied:
        return "投标人提供占位优先于比较符"
    if parsed.operator in {"ge", "le"}:
        return "比较前缀后存在数值"
    if parsed.operator == "range":
        return "数值区间或正负公差"
    if parsed.operator == "eq":
        return "剥离括号条件后为纯数值"
    if parsed.operator == "enum":
        return "存在显式枚举分隔符"
    if parsed.operator == "empty":
        return "空白"
    if parsed.operator == "na":
        return "明确不适用标记"
    return "未匹配数值语法，保留文本"


def test_seed_operator_accuracy(capsys):
    seed = Path(__file__).resolve().parents[5] / "storage/cable-seed/parameters.json"
    rows = json.loads(seed.read_text(encoding="utf-8"))["rows"]
    assert len(rows) == 769
    mismatches = []
    for row in rows:
        parsed = parse_requirement(row["requirement_raw"])
        if parsed.operator != row["operator"]:
            mismatches.append({
                "固化ID": row["solidified_id"],
                "序号": row["seq"],
                "原文": row["requirement_raw"],
                "期望": row["operator"],
                "实际": parsed.operator,
                "推断原因": _inference_reason(parsed),
            })
    accuracy = (len(rows) - len(mismatches)) / len(rows)
    # Disable capture so even a passing -q invocation prints every discrepancy.
    with capsys.disabled():
        print(f"\nSeed operators: {len(rows) - len(mismatches)}/{len(rows)} "
              f"({accuracy:.2%}); mismatches={len(mismatches)}")
        for mismatch in mismatches:
            print(json.dumps(mismatch, ensure_ascii=False))
    assert accuracy >= 0.95, json.dumps(mismatches, ensure_ascii=False, indent=2)


def test_seed_field_diff(capsys):
    """Report baseline differences only; seed semantics are not an oracle."""
    seed = Path(__file__).resolve().parents[5] / "storage/cable-seed/parameters.json"
    rows = json.loads(seed.read_text(encoding="utf-8"))["rows"]
    counts = {"operator": 0, "requirement_value": 0, "unit_condition": 0}
    for row in rows:
        parsed = parse_requirement(row["requirement_raw"])
        actual = {
            "operator": parsed.operator,
            "requirement_value": parsed.value,
            "unit_condition": parsed.unit_condition,
        }
        for field in counts:
            counts[field] += actual[field] != row[field]
    with capsys.disabled():
        print(f"\nSeed field differences ({len(rows)} rows, report only): "
              + json.dumps(counts, ensure_ascii=False))
