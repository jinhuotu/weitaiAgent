"""Hand-labelled gate cases and a separately constructed seed regression."""

from collections import Counter
from dataclasses import replace
import json
from pathlib import Path

import pytest

from apps.api.services.cablepkg.derive import DerivationSkip, derive, load_rules
from apps.api.services.cablepkg.gate import TaskMetadata, evaluate_gate
from apps.api.services.cablepkg.model import EvidenceLevel, Requirement, Response
from apps.api.services.cablepkg.semantics import parse_requirement


TASK = TaskMetadata("独立测试包", ["test-fid"])


def _row(**changes):
    return replace(Response(seq="1", name="独立参数", guarantee_value="4.6", unit="mm",
                            solidified_id="test-fid", evidence_level=EvidenceLevel.L1,
                            basis="企业产品主数据（测试输入）"), **changes)


def test_five_independent_L1_rows_are_biddable_without_reading_seed(monkeypatch):
    # These manually labelled rows are independent of seed data and derive().
    rows = [_row(seq=str(i), is_key=i == 1) for i in range(1, 6)]
    read_text = Path.read_text

    def reject_seed(path, *args, **kwargs):
        assert "cable-seed" not in path.parts, "gate must never read seed"
        return read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", reject_seed)
    result = evaluate_gate(rows, TASK)
    assert result.decision == "biddable"
    assert result.blockers == [] and result.key_blockers == []
    assert result.stats == {
        "total_rows": 5, "applicable_rows": 5, "biddable_rows": 5,
        "evidence_levels": {"L0": 0, "L1": 5, "L2": 0, "L3": 0, "L4": 0},
        "key_non_L0_L1_count": 0, "design_confirmation_count": 0,
        "enterprise_required_count": 0, "na_count": 0, "skip_count": 0,
        "evidence_cannot_fix_count": 0,
        "blocked_row_count": 0, "passed_rows": 5, "blocker_counts": {},
        "blocking_reasons": {"key_parameter": 0, "design_confirmation": 0,
                             "enterprise_required": 0, "master_data": 0,
                             "placeholder": 0, "derivation_skip": 0, "material_mismatch": 0, "key_na_response": 0, "union_rows": 0},
    }
    assert json.loads(json.dumps(result.to_dict())) == result.to_dict()


@pytest.mark.parametrize("level", [EvidenceLevel.L2, EvidenceLevel.L3, EvidenceLevel.L4])
def test_G1_G2_G3_are_separate_and_G2_is_highest_priority(level):
    row = _row(evidence_level=level, is_key=True, bidder_supplied=True,
               basis="独立设计；source=设计手册待核定")
    result = evaluate_gate([row], TASK)
    assert result.decision == "not_biddable"
    assert [b.code for b in result.blockers] == ["G2", "G1", "G3"]
    assert [b.code for b in result.key_blockers] == ["G2"]
    assert result.stats["blocker_counts"] == {"G2": 1, "G1": 1, "G3": 1}
    assert result.stats["key_non_L0_L1_count"] == 1
    assert result.stats["design_confirmation_count"] == 1
    assert result.stats["enterprise_required_count"] == 1
    for blocker in result.blockers:
        assert (blocker.solidified_id, blocker.seq, blocker.name, blocker.row_index) == ("test-fid", "1", "独立参数", 0)
        assert blocker.reason
    assert result.key_blockers[0].priority < result.blockers[1].priority


@pytest.mark.parametrize("value", [None, "", "  ", "响应", "满足", "满足要求", "完全满足要求", "无偏离", "满足用户要求"])
def test_G4_blocks_even_L1_when_value_is_empty_or_hollow(value):
    result = evaluate_gate([_row(guarantee_value=value)], TASK)
    assert result.decision == "not_biddable"
    assert [b.code for b in result.blockers] == ["G4"]
    assert result.stats["biddable_rows"] == 1  # Evidence count, not overall clearance.
    assert result.stats["passed_rows"] == 0


@pytest.mark.parametrize("value", [0, "0", "满足GB/T 2951.38", "满足绝缘厚度4.5mm要求"])
def test_G4_allows_concrete_values(value):
    result = evaluate_gate([_row(guarantee_value=value)], TASK)
    assert result.decision == "biddable" and not result.blockers


def test_G3_is_not_waived_by_L1_evidence():
    result = evaluate_gate([_row(basis="source=设计手册待核定")], TASK)
    assert result.decision == "not_biddable"
    assert [b.code for b in result.blockers] == ["G3"]
    assert result.stats["design_confirmation_count"] == 1


def test_G5_only_explicit_na_is_excluded():
    na = _row(operator="na", is_key=True, bidder_supplied=True, evidence_level=EvidenceLevel.L4,
              guarantee_value=None, basis="source=待核定")
    skip = DerivationSkip("na", "不适用", solidified_id="test-fid", seq="2", name="不适用行")
    result = evaluate_gate([na, skip, _row()], TASK)
    assert result.decision == "biddable"
    assert result.stats["total_rows"] == 3 and result.stats["na_count"] == 2
    assert result.stats["applicable_rows"] == 1
    assert result.stats["evidence_levels"]["L4"] == 0
    assert result.stats["design_confirmation_count"] == 0
    assert result.stats["key_non_L0_L1_count"] == 0
    assert result.stats["enterprise_required_count"] == 0
    placeholder = evaluate_gate([_row(evidence_level=EvidenceLevel.L4, guarantee_value="（待填）")], TASK)
    assert [b.code for b in placeholder.blockers] == ["G1"]
    assert placeholder.stats["na_count"] == 0


@pytest.mark.parametrize("reason", ["no_rule", "invalid_input", "empty", "invalid_value", "invalid_rule"])
def test_G6_skips_need_input_and_retain_locator(reason):
    skip = DerivationSkip(reason, "独立缺失输入", solidified_id="test-fid", seq="7", name="跳过参数",
                          bidder_supplied=True, operator="bidder_supplied")
    result = evaluate_gate([skip], TASK)
    assert result.decision == "needs_input"
    assert [b.code for b in result.blockers] == ["G6"]
    assert result.blockers[0].seq == "7" and result.blockers[0].name == "跳过参数"
    assert reason in result.blockers[0].reason
    assert result.stats["skip_count"] == 1 and result.stats["enterprise_required_count"] == 1
    result = evaluate_gate([replace(skip, is_key=True)], TASK)
    assert result.decision == "needs_input"
    assert [b.code for b in result.blockers] == ["G6"]
    assert result.stats["blocking_reasons"]["key_parameter"] == 0
    assert result.stats["blocking_reasons"]["derivation_skip"] == 1


def test_missing_identity_and_empty_task_cannot_pass():
    assert evaluate_gate([], TASK).decision == "needs_input"
    result = evaluate_gate([_row()], {"tender_package_name": "", "solidified_ids": []})
    assert result.decision == "needs_input"
    assert all(b.code == "G0" for b in result.blockers)
    result = evaluate_gate([_row(solidified_id="")], TaskMetadata("多物料", ["a", "b"]))
    assert result.decision == "needs_input"
    assert result.blockers[0].row_index == 0
    assert evaluate_gate([_row(solidified_id="")], TASK).decision == "needs_input"
    # Response row_index still locates a row with empty descriptive fields;
    # G0 is reserved for task metadata and IDs outside the task list.
    assert evaluate_gate([_row(seq="", name="")], TASK).decision == "biddable"


def test_all_na_requires_input_instead_of_vacuous_success():
    rows = [_row(operator="na"), DerivationSkip("na", "不适用")]
    result = evaluate_gate(rows, TASK)
    assert result.decision == "needs_input"
    assert result.stats["total_rows"] == result.stats["na_count"] == 2
    assert result.stats["applicable_rows"] == 0
    assert [b.code for b in result.blockers] == ["G0"]
    assert result.blockers[0].reason == "无可适用行（全部标记为不适用）"
    assert all(count == 0 for count in result.stats["blocking_reasons"].values())


def test_empty_rows_keeps_existing_G0():
    result = evaluate_gate([], TASK)
    assert result.decision == "needs_input"
    assert [b.code for b in result.blockers] == ["G0"]
    assert result.blockers[0].reason == "尚未提供参数行"


@pytest.mark.parametrize("reason", ["no_rule", "invalid_input"])
def test_skip_without_identity_has_only_G6_located_by_row_index(reason):
    rows = [_row(), DerivationSkip(reason, "缺少输入", is_key=True)]
    result = evaluate_gate(rows, TaskMetadata("多物料", ["test-fid", "other-fid"]))
    assert result.decision == "needs_input"
    assert [b.code for b in result.blockers] == ["G6"]
    assert result.blockers[0].row_index == 1
    assert result.blockers[0].solidified_id == ""
    assert result.stats["blocked_row_count"] == 1


def test_independent_overlapping_reason_counts():
    # Hand-labelled memberships: row0=key+design+enterprise+placeholder,
    # row1=placeholder only, row2=master, row3=no reason, row4=NA excluded.
    rows = [
        _row(evidence_level=EvidenceLevel.L4, is_key=True, bidder_supplied=True, basis="source=独立设计"),
        _row(evidence_level=EvidenceLevel.L4),
        _row(evidence_level=EvidenceLevel.L2),
        _row(),
        _row(operator="na", evidence_level=EvidenceLevel.L4, is_key=True, bidder_supplied=True, basis="source=忽略"),
    ]
    result = evaluate_gate(rows, TASK)
    assert result.stats["blocking_reasons"] == {
        "key_parameter": 1, "design_confirmation": 1, "enterprise_required": 1,
        "master_data": 1, "placeholder": 2, "derivation_skip": 0, "material_mismatch": 0, "key_na_response": 0, "union_rows": 3,
    }


def test_independent_L4_is_placeholder_not_master_data():
    result = evaluate_gate([_row(evidence_level=EvidenceLevel.L4)], TASK)
    assert result.stats["blocking_reasons"] == {
        "key_parameter": 0, "design_confirmation": 0, "enterprise_required": 0,
        "master_data": 0, "placeholder": 1, "derivation_skip": 0, "material_mismatch": 0, "key_na_response": 0, "union_rows": 1,
    }


@pytest.mark.parametrize("flags", [{}, {"is_key": True, "bidder_supplied": True, "operator": "bidder_supplied"}])
def test_independent_skip_only_enters_derivation_skip(flags):
    result = evaluate_gate([DerivationSkip("invalid_input", "独立缺失输入", **flags)], TASK)
    assert result.stats["blocking_reasons"] == {
        "key_parameter": 0, "design_confirmation": 0, "enterprise_required": 0,
        "master_data": 0, "placeholder": 0, "derivation_skip": 1, "material_mismatch": 0, "key_na_response": 0, "union_rows": 1,
    }


def test_derive_preserves_row_flags_for_response_and_skip():
    req = Requirement(name="导体外径", seq="1.5", operator="bidder_supplied", is_key=True)
    material = {"fid": "test-fid", "model": "YJV22", "cond": "铜", "cores": 3, "area": 150}
    for requirement in (req, replace(req, name="未知参数")):
        out = derive(requirement, material)
        assert (out.solidified_id, out.seq, out.name, out.is_key, out.bidder_supplied, out.operator) == (
            "test-fid", "1.5", requirement.name, True, True, "bidder_supplied")
        assert out.to_dict()["solidified_id"] == "test-fid"


@pytest.mark.parametrize("model,cond,raw,expected_operator,expected_value,expected_condition", [
    ("YJV22", "铜", "小于等于0.0778（对应400mm2铝截面）", "le", 0.0778, "对应400mm2铝截面"),
    ("YJLV22", "铝", "小于等于0.0470（对应400mm2铜截面）", "le", 0.047, "对应400mm2铜截面"),
])
def test_independent_G7_conductor_conflict(model, cond, raw, expected_operator, expected_value, expected_condition):
    req = replace(parse_requirement(raw), name="20℃时导体最大直流电阻", seq="1.42", solidified_id="test-fid")
    assert (req.operator, req.value, req.unit_condition, req.qualifier) == (expected_operator, expected_value, expected_condition, None)
    row = _row(name=req.name, seq=req.seq, guarantee_value="0.04")
    result = evaluate_gate([row], TASK, requirements=[req], materials={"test-fid": {"model": model, "cond": cond}})
    assert result.decision == "not_biddable"
    assert [b.code for b in result.blockers] == ["G7"]
    assert result.blockers[0].priority == 0
    assert (result.blockers[0].solidified_id, result.blockers[0].seq) == ("test-fid", "1.42")
    assert result.stats["blocking_reasons"]["material_mismatch"] == 1
    assert result.stats["blocking_reasons"]["union_rows"] == 1


@pytest.mark.parametrize("name,raw", [
    ("金属屏蔽材料", "铜带"), ("金属屏蔽材料", "铜丝"),
    ("导体电阻", "采用铜带屏蔽"), ("导体电阻", "采用铜丝屏蔽"),
    ("导体材料", "铜或铝"),
])
def test_G7_excludes_screen_components_and_allowed_material_choices(name, raw):
    req = Requirement(name=name, raw=raw)
    result = evaluate_gate([_row(name=name)], TASK, requirements=[req], materials={"test-fid": {"model": "YJLV22", "cond": "铝"}})
    assert result.decision == "biddable" and not result.blockers
    assert result.stats["blocking_reasons"]["material_mismatch"] == 0


@pytest.mark.parametrize("value", ["不适用", "不适用（采用铜带屏蔽）", "不涉及", "无", "—", "无此项"])
def test_independent_G8_key_negative_response_cannot_be_fixed_by_evidence(value):
    # L1 deliberately isolates G8: more evidence cannot clear a design denial.
    result = evaluate_gate([_row(is_key=True, guarantee_value=value)], TASK)
    assert result.decision == "not_biddable"
    assert [b.code for b in result.blockers] == ["G8"]
    assert result.blockers[0].priority == 0
    assert result.blockers[0].evidence_cannot_fix is True
    assert result.to_dict()["blockers"][0]["evidence_cannot_fix"] is True
    assert result.stats["evidence_cannot_fix_count"] == 1
    assert result.stats["blocking_reasons"]["key_na_response"] == 1
    assert result.stats["blocking_reasons"]["union_rows"] == 1


def test_non_key_negative_response_and_concrete_key_response_do_not_trigger_G8():
    result = evaluate_gate([_row(guarantee_value="不适用"), _row(is_key=True, guarantee_value="无卤阻燃材料")], TASK)
    assert result.decision == "biddable"
    assert not result.blockers and result.stats["evidence_cannot_fix_count"] == 0
    assert result.stats["blocking_reasons"]["key_na_response"] == 0


def test_G8_cannot_be_hidden_by_explicit_na():
    result = evaluate_gate([_row(is_key=True, operator="na", guarantee_value="不涉及")], TASK)
    assert result.decision == "not_biddable"
    assert [b.code for b in result.blockers] == ["G8"]


def test_G7_requires_index_aligned_inputs():
    with pytest.raises(ValueError, match="align"):
        evaluate_gate([_row()], TASK, requirements=[], materials={})


def test_seed_gate_regression(capsys):
    # Seed is read only here, by the caller; the gate receives fully built rows.
    data = json.loads((Path(__file__).resolve().parents[5] / "storage/cable-seed/parameters.json").read_text(encoding="utf-8"))
    materials = {m["fid"]: m for m in data["materials"]}
    policy = load_rules()
    rows, requirements = [], []
    for row in data["rows"]:
        req = replace(parse_requirement(row["requirement_raw"]), name=row["name"], seq=row["seq"],
                      unit=row["unit"], is_key=row["is_key"], needs_report=row["needs_report"])
        rows.append(derive(req, materials[row["solidified_id"]], rules=policy))
        requirements.append(req)
    result = evaluate_gate(rows, TaskMetadata("10kV电力电缆参数包", list(materials)),
                           requirements=requirements, materials=materials)
    assert result.decision == "not_biddable"
    # All 80 L4 placeholders are applicable: no explicit NA is present.
    assert result.stats["total_rows"] == result.stats["blocked_row_count"] == 769
    assert result.stats["na_count"] == 0
    assert result.stats["evidence_levels"] == {"L0": 0, "L1": 0, "L2": 209, "L3": 480, "L4": 80}
    assert result.stats["key_non_L0_L1_count"] == len(result.key_blockers) == 30
    assert result.stats["design_confirmation_count"] == 130
    assert result.stats["enterprise_required_count"] == 251
    assert result.stats["blocking_reasons"] == {
        "enterprise_required": 251, "design_confirmation": 130,
        "key_parameter": 30, "master_data": 349, "placeholder": 80, "derivation_skip": 0, "material_mismatch": 1, "key_na_response": 10, "union_rows": 769,
    }
    assert result.stats["blocker_counts"] == {"G2": 30, "G1": 769, "G3": 130, "G7": 1, "G8": 10}
    assert result.stats["evidence_cannot_fix_count"] == 10
    assert [(b.solidified_id, b.seq) for b in result.blockers if b.code == "G7"] == [("9985-500108211-00001", "1.42")]
    g8 = [b for b in result.blockers if b.code == "G8"]
    assert len(g8) == 10 and {b.seq for b in g8} == {"1.20"}
    assert {b.solidified_id for b in g8} == set(materials)
    assert all(b.evidence_cannot_fix for b in g8)
    assert {b.row_index for b in result.blockers} == set(range(769))
    assert {b.row_index for b in result.key_blockers} == {i for i, row in enumerate(data["rows"]) if row["is_key"]}
    assert {b.row_index for b in result.blockers if b.code == "G3"} == {i for i, row in enumerate(rows) if "source=" in row.basis}
    assert Counter(b.code for b in result.blockers if b.priority == 0) == {"G2": 30, "G7": 1, "G8": 10}
    assert all(b.priority == 0 for b in result.blockers[:41])
    assert all(b.solidified_id and b.seq and b.name and b.reason for b in result.blockers)
    with capsys.disabled():
        print("\nGate stats: " + json.dumps(result.stats, ensure_ascii=False))
        serialized = result.to_dict()["blockers"]
        for code in ("G1", "G2", "G3"):
            print(f"{code} examples: " + json.dumps([b for b in serialized if b["code"] == code][:3], ensure_ascii=False))
