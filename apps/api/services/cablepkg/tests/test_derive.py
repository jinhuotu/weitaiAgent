"""Independent hand-calculated cases plus a separate seed regression report."""

from collections import Counter
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re

import pytest

from apps.api.services.cablepkg.derive import DerivationSkip, derive, load_rules
from apps.api.services.cablepkg.model import EvidenceLevel, Requirement, Response
from apps.api.services.cablepkg.semantics import parse_requirement


MATERIAL = {"model": "YJV22", "cond": "铜", "cores": 3, "area": 150}


@pytest.mark.parametrize("raw,name,unit,expected,level,operator,value,condition,qualifier", [
    ("无", "导体外径", "mm", None, None, "na", None, None, None),
    ("大于等于80%标称厚度（有铠装）", "隔离套最小厚度", "mm", "1.6", "L3", "ge", 80, "有铠装", "%标称厚度"),
    ("(投标人提供)", "挤包导体屏蔽平均厚度", "mm", "0.8", "L3", "bidder_supplied", None, "投标人提供", None),
    ("大于等于4.5(10kV)", "绝缘平均厚度", "mm", "4.6", "L3", "ge", 4.5, "10kV", None),
    ("大于等于12.5", "绝缘老化前抗张强度", "MPa", "13.5", "L3", "ge", 12.5, None, None),
    ("大于等于90%标称厚度", "外护套最薄点厚度", "mm", "2.88", "L3", "ge", 90, None, "%标称厚度"),
    ("小于等于0.0008", "tanδ", "", "0.0004", "L3", "le", 0.0008, None, None),
    ("PVC或PE", "外护套材料", "", "聚氯乙烯", "L2", "enum", ["PVC", "PE"], None, None),
])
def test_independent_design_cases(raw, name, unit, expected, level, operator, value, condition, qualifier):
    # Hand annotations: 2mm×80%=1.6mm; 4.5+0.1=4.6mm;
    # 12.5+1=13.5MPa; 3.2mm×90%=2.88mm; 0.0008×0.5=0.0004.
    req = replace(parse_requirement(raw), name=name, unit=unit, seq="independent")
    assert (req.operator, req.value, req.unit_condition, req.qualifier) == (operator, value, condition, qualifier)
    out = derive(req, MATERIAL)
    if expected is None:
        assert isinstance(out, DerivationSkip)
        assert (out.reason, out.detail) == ("na", "不适用")
        assert out.seq == "independent" and out.name == name and out.operator == "na"
    else:
        assert isinstance(out, Response)
        assert out.guarantee_value == expected
        assert out.evidence_level.value == level
        assert out.seq == "independent" and out.unit == unit and out.name == name
        assert out.basis


def test_geometry_and_time_are_calculated_not_looked_up():
    # Independently evaluated sqrt(4*150/(pi*0.9)) = 14.5673... mm.
    req = Requirement(name="导体外径", operator="bidder_supplied")
    assert derive(req, MATERIAL).guarantee_value == "14.6"
    # 143*150/sqrt(3)/1000 = 12.384... kA at 3s, NOT 21.45kA (1s).
    req.name = "导体允许短路电流（250℃）(3s)"
    assert derive(req, MATERIAL).guarantee_value == "12.4"
    policy = load_rules()
    req.name = "导体允许短路电流（250℃）(1s)"
    out = derive(req, MATERIAL, rules=policy)
    assert out.guarantee_value == "21.4"
    assert "绝热法，k=143，t=1s" in out.basis


def test_external_policy_and_safety():
    policy = load_rules()
    req = Requirement(name="新参数", operator="text", value="custom")
    assert derive(req, MATERIAL, rules=policy).reason == "no_rule"
    policy["rules"].insert(0, {"id": "custom", "name_pattern": "^新参数$", "action": "constant",
                                "params": {"value": 7}, "level": "L2", "basis": "独立自定义规则"})
    assert derive(req, MATERIAL, rules=policy).guarantee_value == "7"
    before = deepcopy(policy)
    assert derive(req, MATERIAL, rules=policy).to_dict() == derive(req, MATERIAL, rules=policy).to_dict()
    assert policy == before
    req.operator = "bidder_supplied"
    assert derive(req, MATERIAL, rules=policy).evidence_level == EvidenceLevel.L3
    req.operator = "text"
    for level in ("L0", "L1"):
        policy["rules"][0]["level"] = level
        assert derive(req, MATERIAL, rules=policy).reason == "invalid_rule"
    policy["rules"][0]["level"] = "L2"
    for value in ("响应", "满足", "满足要求", "完全满足", "完全满足要求", "无偏离", "满足用户要求"):
        policy["rules"][0]["params"]["value"] = value
        assert derive(req, MATERIAL, rules=policy).reason == "invalid_value"
    for value in ("满足GB/T 2951.38", "满足绝缘厚度4.5mm要求"):
        policy["rules"][0]["params"]["value"] = value
        assert derive(req, MATERIAL, rules=policy).guarantee_value == value


def test_percentage_cannot_fall_through_to_millimetres():
    req = replace(parse_requirement("大于等于90%标称厚度"), name="绝缘平均厚度", unit="mm")
    assert derive(req, MATERIAL).reason == "invalid_input"
    assert derive(Requirement(), MATERIAL).reason == "empty"
    assert derive(Requirement(name="未知参数", operator="enum", value=["A", "B"]), MATERIAL).reason == "no_rule"


def _same_value(actual, expected):
    # Ignore only numeric presentation (e.g. 0.80 == 0.8); no tolerance or
    # dropped rows. All textual values must match exactly.
    try:
        return Decimal(str(actual)) == Decimal(str(expected))
    except InvalidOperation:
        return actual == expected


# Independent hand-calculated cases for every conductor short-current erratum.
# k is A*sqrt(s)/mm²; I(kA)=k*S/sqrt(3)/1000. These literals are
# annotated independently of rules_cable.json, derive(), and the seed values.
SHORT_CASES = [
    ("铝", 120, 6.5), ("铝", 300, 16.3),
    ("铜", 70, 5.8), ("铜", 120, 9.9), ("铜", 150, 12.4),
    ("铜", 240, 19.8), ("铜", 300, 24.8), ("铜", 400, 33.0),
]


@pytest.mark.parametrize("cond,area,expected", SHORT_CASES)
def test_independent_conductor_short_circuit(cond, area, expected):
    # In particular: 143*400/sqrt(3)/1000=33.024... => 33.0kA ±0.2.
    req = Requirement(name="导体允许短路电流（250℃）(3s)", operator="bidder_supplied")
    out = derive(req, {**MATERIAL, "cond": cond, "area": area})
    assert float(out.guarantee_value) == pytest.approx(expected, abs=0.05)
    assert out.evidence_level == EvidenceLevel.L3
    k = 143 if cond == "铜" else 94
    assert f"绝热法，k={k}，t=3s" in out.basis


def test_independent_screen_short_circuit_and_geometry_parameters():
    # A synthetic geometry: conductor D=10mm, core D=20mm; circumference=20π,
    # axial pitch=20π; width=10/sqrt(2), thickness=2 => Aeq=20mm².
    # Therefore 143*20/sqrt(3)/1000=1.651...kA (3s), 2.86kA (1s).
    policy = load_rules()
    policy["geometry"].update(compaction=1, core_layers=[5],
        tape_width=7.0710678118654755, screen_thickness=2,
        winding_pitch=78.53981633974483, overlap=0.2)
    material = {**MATERIAL, "area": 78.53981633974483, "cores": 1}
    req = Requirement(name="金属屏蔽允许短路电流(3s)", operator="bidder_supplied")
    out = derive(req, material, rules=policy)
    assert float(out.guarantee_value) == pytest.approx(1.65, abs=0.005)
    assert "绝热法，k=143，t=3s" in out.basis
    assert "source=设计手册待核定" in out.basis
    assert out.evidence_level == EvidenceLevel.L3
    changed_pitch = deepcopy(policy)
    changed_pitch["geometry"]["winding_pitch"] *= 2
    # Aeq=10*sqrt(2)*sqrt(1+1/4)=15.811...mm² -> 1.31kA.
    assert derive(req, material, rules=changed_pitch).guarantee_value == "1.31"
    changed_overlap = deepcopy(policy)
    changed_overlap["geometry"]["overlap"] = 0.6
    # Half the axial pitch: Aeq=10*sqrt(2)*sqrt(5) -> 2.61kA.
    assert derive(req, material, rules=changed_overlap).guarantee_value == "2.61"
    req.name = "金属屏蔽允许短路电流(1s)"
    assert derive(req, material, rules=policy).guarantee_value == "2.86"
    req.name = "金属屏蔽允许短路电流(2s)"
    assert derive(req, material, rules=policy).reason == "invalid_input"
    # Changing tape width halves area and hence halves current (before rounding).
    req.name = "金属屏蔽允许短路电流(3s)"
    policy["geometry"]["tape_width"] /= 2
    assert derive(req, material, rules=policy).guarantee_value == "0.83"
    # Single-core extra diametric gap is explicitly 0.6mm, not a hidden constant.
    req.name = "铠装层外径"
    with_gap = float(derive(req, material, rules=policy).guarantee_value)
    policy["geometry"]["single_core_gap"] = 0
    without_gap = float(derive(req, material, rules=policy).guarantee_value)
    assert with_gap - without_gap == pytest.approx(0.6)


def _difference_category(response):
    rule_id = response.basis.split(":", 1)[0]
    if rule_id == "R5.short_conductor":
        return "seed_wrong", "seed标3s但数值对应kS；独立算例按kS/√3核验"
    if rule_id == "R5.special":
        return "wording", "同一GB/T 2951.38技术内容的措辞简化，不视为空洞应答"
    if "source=设计手册待核定" in response.basis:
        return "geometry_pending_design", "等效铜带截面/结构口径须企业结构图确认"
    return "unclassified", "未解释差异，须人工复核"


def _regression_report():
    seed = Path(__file__).resolve().parents[5] / "storage/cable-seed/parameters.json"
    data = json.loads(seed.read_text(encoding="utf-8"))
    materials = {m["fid"]: m for m in data["materials"]}
    rules = load_rules()
    differences, skips, errata = [], [], []
    levels, categories = Counter(), Counter()
    bidder_count = key_count = overlap_count = matched = design_pending_count = 0
    independent = {(cond, area): value for cond, area, value in SHORT_CASES}
    for row in data["rows"]:
        material = materials[row["solidified_id"]]
        req = replace(parse_requirement(row["requirement_raw"]), name=row["name"], seq=row["seq"],
                      unit=row["unit"], is_key=row["is_key"], needs_report=row["needs_report"])
        out = derive(req, material, rules=rules)
        identity = {"solidified_id": row["solidified_id"], "seq": row["seq"], "name": row["name"], "raw": req.raw}
        if isinstance(out, DerivationSkip):
            skips.append({**identity, **out.to_dict()})
            continue
        assert out.evidence_level in {EvidenceLevel.L2, EvidenceLevel.L3, EvidenceLevel.L4}
        assert not any(re.fullmatch(pattern, out.guarantee_value.strip()) for pattern in rules["forbidden_patterns"])
        if req.bidder_supplied or "source=" in out.basis:
            assert out.evidence_level == EvidenceLevel.L3
        levels[out.evidence_level.value] += 1
        # Independent of seed agreement: matching a seed value never clears source=.
        design_pending_count += "source=" in out.basis
        bidder_count += req.bidder_supplied
        key = req.is_key and out.evidence_level in {EvidenceLevel.L2, EvidenceLevel.L3}
        key_count += key
        overlap_count += bool(key and req.bidder_supplied)
        if _same_value(out.guarantee_value, row["guarantee_value"]):
            matched += 1
        else:
            category, reason = _difference_category(out)
            categories[category] += 1
            differences.append({**identity, "seed": row["guarantee_value"], "actual": out.guarantee_value,
                                "category": category, "reason": reason})
            if category == "seed_wrong":
                # Every erratum must resolve to an independently tested vector.
                expected = independent[(material["cond"], material["area"])]
                assert float(out.guarantee_value) == pytest.approx(expected, abs=0.05)
                k = 143 if material["cond"] == "铜" else 94
                errata.append({**identity, "seed_value": row["guarantee_value"],
                    "correct_value": expected, "unit": "kA", "evidence_level": "L3",
                    "evidence": f"绝热法，k={k}，S={material['area']}mm²，t=3s；I=k*S/√3/1000 kA",
                    "independent_assertion": {"test": "test_independent_conductor_short_circuit",
                        "cond": material["cond"], "area": material["area"], "expected_kA": expected}})
    return {"total": len(data["rows"]), "matched": matched, "no_rule": sum(s["reason"] == "no_rule" for s in skips),
            "skips": skips, "levels": dict(levels), "categories": dict(categories),
            "bidder_supplied": bidder_count, "key_L2_L3": key_count,
            "设计待确认行数": design_pending_count,
            "enterprise_required": bidder_count + key_count - overlap_count,
            "differences": differences, "errata": errata}


def test_seed_derivation_regression(capsys):
    report = _regression_report()
    with capsys.disabled():
        print("\nDerivation regression: " + json.dumps({k: v for k, v in report.items()
              if k not in {"differences", "errata"}}, ensure_ascii=False))
        for difference in report["differences"]:
            if difference["category"] == "geometry_pending_design":
                continue
            print(json.dumps(difference, ensure_ascii=False))
        print("geometry_pending_design complete list (requires enterprise structure drawings):")
        for difference in report["differences"]:
            if difference["category"] == "geometry_pending_design":
                print(json.dumps(difference, ensure_ascii=False))
    assert report["total"] == 769
    assert report["no_rule"] == 0
    assert len(report["skips"]) == 0
    assert set(report["categories"]) <= {"seed_wrong", "geometry_pending_design", "wording"}
    assert report["bidder_supplied"] == 251 and report["key_L2_L3"] == 30
    assert report["设计待确认行数"] == 130
    errata_path = Path(__file__).resolve().parents[5] / "storage/cable-seed/errata.json"
    artifact = json.loads(errata_path.read_text(encoding="utf-8"))
    assert artifact["rows"] == report["errata"]
    assert len(artifact["rows"]) == report["categories"].get("seed_wrong", 0)
