"""审批流程定义与按角色审批。"""

from __future__ import annotations

from types import SimpleNamespace

from api.services.approval_flow import (
    KEY_DONE,
    default_steps,
    find_step,
    next_step_on_pass,
    normalize_steps,
    review_steps,
    user_can_act_on_record,
    user_can_act_on_step,
)


def test_default_steps_and_pass_chain():
    steps = default_steps()
    names = [item["name"] for item in steps]
    assert names == ["提交申请", "部门经理审批", "总经理审批", "财务审核", "完成"]
    assert next_step_on_pass("部门经理审批", steps) == ("pending", "review_gm")
    assert next_step_on_pass("review_dept", steps) == ("pending", "review_gm")
    assert next_step_on_pass("财务审核", steps) == ("approved", KEY_DONE)
    assert next_step_on_pass("review_finance", steps) == ("approved", KEY_DONE)


def test_normalize_keeps_custom_review_and_bookends():
    steps = normalize_steps(
        [
            {"key": "rev_a", "name": "科室审核", "kind": "review", "roleCode": "operator"},
            {"key": "rev_b", "name": "分管领导", "kind": "review", "roleCode": "auditor"},
        ]
    )
    reviews = review_steps(steps)
    assert [item["name"] for item in reviews] == ["科室审核", "分管领导"]
    assert steps[0]["key"] == "submit"
    assert steps[-1]["key"] == "done"
    assert next_step_on_pass("rev_a", steps) == ("pending", "rev_b")


def test_unbound_step_allows_any_non_superuser():
    step = {"key": "review_dept", "name": "部门经理审批", "kind": "review", "roleCode": None}
    user = SimpleNamespace(is_superuser=False, roles=[SimpleNamespace(code="operator")])
    assert user_can_act_on_step(user, step) is True


def test_bound_step_requires_role_or_superuser():
    step = {"key": "review_gm", "name": "总经理审批", "kind": "review", "roleCode": "auditor"}
    operator = SimpleNamespace(is_superuser=False, roles=[SimpleNamespace(code="operator")])
    auditor = SimpleNamespace(is_superuser=False, roles=[SimpleNamespace(code="auditor")])
    boss = SimpleNamespace(is_superuser=True, roles=[])
    assert user_can_act_on_step(operator, step) is False
    assert user_can_act_on_step(auditor, step) is True
    assert user_can_act_on_step(boss, step) is True


def test_pending_filter_uses_snapshot_or_default_names():
    operator = SimpleNamespace(is_superuser=False, roles=[SimpleNamespace(code="operator")])
    snapshot = {
        "steps": default_steps(),
    }
    snapshot["steps"][1]["roleCode"] = "auditor"
    assert user_can_act_on_record(operator, snapshot, "review_dept") is False
    assert user_can_act_on_record(operator, None, "部门经理审批") is True


def test_find_step_matches_legacy_chinese_name():
    step = find_step(default_steps(), "总经理审批")
    assert step is not None
    assert step["key"] == "review_gm"
