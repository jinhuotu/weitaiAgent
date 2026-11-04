"""v2 layout_out 准备：条件表执法 + 程序 pack；有上一张时只改用户点名的项。"""

from __future__ import annotations

from typing import Any

from api.services.layouts.brief import (
    apply_equipment_additions,
    apply_gate_from_texts,
    attach_site_polygon,
    parse_layout_brief,
    prune_unasked_transformers,
    prune_unmentioned_site_context,
)
from api.services.layouts.check import CheckIssue, issues_as_notes, repair_plan
from api.services.layouts.pack import pin_rows_against_walls, touch_up_plan
from api.services.layouts.parse import parse_plan
from api.services.layouts.render import prepare_plan
from api.services.layouts.revise import (
    is_equipment_only_revise,
    lock_site_geometry,
    preserve_parking_layout,
    revise_plan_from_prior,
    should_relayout_equipment,
    user_asked_to_move_gate,
)
from api.services.layouts.rules import default_sheet_notes
from api.services.layouts.schema import EvChargingStationPlan
from api.services.layouts.v2.constraints import (
    CONSTRAINTS_KIND,
    LayoutConstraints,
    apply_constraint_hints,
    bind_constraints_to_prior,
    check_constraints,
    constraints_to_brief,
    merge_gate_along_from_context,
    overlay_user_text_on_constraints,
    seed_plan_payload,
)

_FEEDER = {
    "ring_cabinet",
    "box_transformer",
    "ring_box_transformer",
    "lv_cabinet",
}


def _corner_rows_block_egress(plan: EvChargingStationPlan) -> bool:
    from api.services.layouts.pack import _infer_row_wall

    walls = {_infer_row_wall(plan, row) for row in plan.parkingRows} - {None}
    adjacent = (
        {"north", "west"},
        {"north", "east"},
        {"south", "west"},
        {"south", "east"},
    )
    return any(pair <= walls for pair in adjacent)


def _parse_or_seed_plan(
    payload: Any, constraints: LayoutConstraints, *, query: str
) -> EvChargingStationPlan:
    """模型 JSON 损坏或只回了条件表时，用条件表种子出图，避免卡在「JSON 无法解析」。"""
    if isinstance(payload, dict) and str(payload.get("kind") or "") == CONSTRAINTS_KIND:
        return parse_plan(seed_plan_payload(constraints, query=query))
    try:
        plan = parse_plan(payload)
    except Exception:  # noqa: BLE001
        return parse_plan(seed_plan_payload(constraints, query=query))
    if not plan.parkingRows:
        return parse_plan(seed_plan_payload(constraints, query=query))
    return plan


def _restore_feeders(plan: EvChargingStationPlan, prior: EvChargingStationPlan) -> None:
    prior_feed = [eq for eq in prior.equipment if eq.type in _FEEDER]
    cur_feed = [eq for eq in plan.equipment if eq.type in _FEEDER]
    others = [eq for eq in plan.equipment if eq.type not in _FEEDER]
    if not prior_feed or not cur_feed:
        return
    merged = []
    for i, eq in enumerate(cur_feed):
        src = prior_feed[min(i, len(prior_feed) - 1)]
        merged.append(eq.model_copy(update={"x": src.x, "y": src.y, "id": eq.id or src.id}))
    plan.equipment = merged + others


def prepare_v2_plan(
    payload: Any,
    *,
    query: str,
    vision: str,
    constraints: LayoutConstraints,
    prior: Any = None,
    revise: bool = False,
    draft: Any = None,
) -> tuple[EvChargingStationPlan, list[CheckIssue]]:
    prior_plan: EvChargingStationPlan | None = None
    if isinstance(prior, EvChargingStationPlan):
        prior_plan = prior
    elif isinstance(prior, dict) and prior.get("kind") == "ev_charging_station_plan":
        try:
            prior_plan = parse_plan(prior)
        except Exception:  # noqa: BLE001
            prior_plan = None

    if prior_plan is not None:
        constraints = bind_constraints_to_prior(constraints, prior_plan, query=query)
    else:
        from api.services.layouts.revise import parse_charger_type_hint

        asked_dc = parse_charger_type_hint(query)
        from api.services.layouts.revise import _parse_stall_range

        if (
            asked_dc in {"dc_320kw", "dc_160kw", "dc_120kw"}
            and _parse_stall_range(query) is None
        ):
            constraints = constraints.model_copy(deep=True)
            constraints.chargers.dcType = asked_dc  # type: ignore[assignment]

    constraints = overlay_user_text_on_constraints(
        constraints, query, vision, overwrite_counts=prior_plan is None
    )
    from api.services.layouts.draft import (
        fill_fleet_from_draft,
        overlay_draft_on_plan,
        parse_parking_rows_from_text,
        should_trace_draft,
    )

    draft_plan: EvChargingStationPlan | None = None
    if isinstance(draft, EvChargingStationPlan):
        draft_plan = draft
    elif isinstance(draft, dict) and draft.get("kind") == "ev_charging_station_plan":
        try:
            draft_plan = parse_plan(draft)
        except Exception:  # noqa: BLE001
            draft_plan = None
    vis_rows = parse_parking_rows_from_text(vision)
    constraints = fill_fleet_from_draft(constraints, draft_plan, vis_rows)
    constraints = merge_gate_along_from_context(
        constraints, query=query, vision=vision, prior=prior_plan
    )
    from api.services.layouts.brief import parse_site_polygon

    poly = parse_site_polygon(query, vision)
    if len(poly) >= 5:
        constraints = constraints.model_copy(deep=True)
        constraints.site.shapeFrom = "vision"
    lock_env = constraints.site.shapeFrom in {"user_rect", "vision"}

    if revise and prior_plan is not None:
        llm_plan = None
        try:
            llm_plan = parse_plan(payload)
        except Exception:  # noqa: BLE001
            llm_plan = None
        plan = revise_plan_from_prior(prior_plan, query=query, llm_plan=llm_plan)
        user_brief = parse_layout_brief(query)
        from api.services.layouts.revise import _parse_stall_range

        # 点名 1-8 号改成重卡时，repair_plan 会按整场车队重排，冲掉局部改图
        if _parse_stall_range(query) is None and (
            user_brief.cars is not None or user_brief.trucks is not None
        ):
            plan = repair_plan(plan, constraints_to_brief(constraints))
            plan = lock_site_geometry(plan, prior_plan, keep_gate=user_asked_to_move_gate(query))
        plan = apply_constraint_hints(plan, constraints, query=query)
        plan = apply_gate_from_texts(plan, query, vision)
        from api.services.layouts.revise import apply_query_charger_to_plan

        apply_query_charger_to_plan(plan, query)
        equip_only = is_equipment_only_revise(query, user_brief)
        from api.services.layouts.revise import _parse_stall_range, parse_charger_type_hint

        charger_only = (
            parse_charger_type_hint(query) is not None
            and _parse_stall_range(query) is not None
            and user_brief.cars is None
            and user_brief.trucks is None
        )
        local_stalls = _parse_stall_range(query) is not None
        gate_only = (
            user_asked_to_move_gate(query)
            and user_brief.cars is None
            and user_brief.trucks is None
            and not should_relayout_equipment(query, user_brief)
        )
        keep_rows = equip_only or charger_only or local_stalls or gate_only
        plan = pin_rows_against_walls(
            plan,
            preferred_side=constraints.layout.wallSide,
            lock_envelope=lock_env,
            gentle=keep_rows,
            preserve_rows=keep_rows,
        )
        plan = touch_up_plan(
            plan, lock_envelope=lock_env, gentle=keep_rows, preserve_rows=keep_rows
        )
        apply_query_charger_to_plan(plan, query)
        from api.services.layouts.pack import sync_charger_annotations

        sync_charger_annotations(plan)
        if not should_relayout_equipment(query, user_brief) and not _corner_rows_block_egress(
            prior_plan
        ):
            _restore_feeders(plan, prior_plan)
            from api.services.layouts.pack import _clamp_equipment

            _clamp_equipment(plan)
        apply_query_charger_to_plan(plan, query)
        sync_charger_annotations(plan)
        issues = check_constraints(plan, constraints)
        notes = [n for n in (plan.notes or []) if not str(n).startswith("校验未通过")]
        notes.extend(default_sheet_notes(query))
        notes.extend(issues_as_notes(issues))
        plan.notes = notes[:12]
        return plan, issues

    plan = _parse_or_seed_plan(payload, constraints, query=query)
    trace = should_trace_draft(query, bool((draft_plan and draft_plan.parkingRows) or vis_rows))
    if trace:
        plan = overlay_draft_on_plan(plan, draft_plan, vis_rows)
    plan = attach_site_polygon(plan, query, vision)
    if draft_plan is not None and len(draft_plan.site.polygon or []) >= 3 and trace:
        plan = overlay_draft_on_plan(plan, draft_plan, vis_rows)
    if len(plan.site.polygon or []) >= 3:
        lock_env = True
    plan = prune_unmentioned_site_context(plan, query, vision)
    if prior_plan is not None:
        user_brief = parse_layout_brief(query)
        plan = lock_site_geometry(plan, prior_plan, keep_gate=user_asked_to_move_gate(query))
        plan = preserve_parking_layout(plan, prior_plan)
    brief = constraints_to_brief(constraints)
    plan = repair_plan(plan, brief)
    apply_equipment_additions(plan, parse_layout_brief(query))
    prune_unasked_transformers(plan, query)
    plan = apply_constraint_hints(plan, constraints, query=query)
    plan = apply_gate_from_texts(plan, query, vision)
    from api.services.layouts.revise import apply_query_charger_to_plan

    apply_query_charger_to_plan(plan, query)
    plan = prepare_plan(
        plan, lock_envelope=lock_env, query=query, gentle=trace, preserve_rows=trace
    )
    plan = pin_rows_against_walls(
        plan,
        preferred_side=constraints.layout.wallSide,
        lock_envelope=lock_env,
        gentle=trace,
        preserve_rows=trace,
    )
    plan = touch_up_plan(plan, lock_envelope=lock_env, gentle=trace, preserve_rows=trace)
    apply_query_charger_to_plan(plan, query)
    from api.services.layouts.pack import sync_charger_annotations

    sync_charger_annotations(plan)
    issues = check_constraints(plan, constraints)
    blocking = [i for i in issues if i.blocking]
    if blocking:
        plan = repair_plan(plan, brief)
        apply_equipment_additions(plan, parse_layout_brief(query))
        prune_unasked_transformers(plan, query)
        plan = apply_constraint_hints(plan, constraints, query=query)
        plan = attach_site_polygon(plan, query, vision)
        if draft_plan is not None and len(draft_plan.site.polygon or []) >= 3 and trace:
            plan = overlay_draft_on_plan(plan, draft_plan, vis_rows)
        plan = apply_gate_from_texts(plan, query, vision)
        if prior_plan is not None:
            user_brief = parse_layout_brief(query)
            plan = lock_site_geometry(plan, prior_plan, keep_gate=user_asked_to_move_gate(query))
            plan = preserve_parking_layout(plan, prior_plan)
        apply_query_charger_to_plan(plan, query)
        plan = prepare_plan(
            plan, lock_envelope=lock_env, query=query, gentle=trace, preserve_rows=trace
        )
        plan = pin_rows_against_walls(
            plan,
            preferred_side=constraints.layout.wallSide,
            lock_envelope=lock_env,
            gentle=trace,
            preserve_rows=trace,
        )
        plan = touch_up_plan(plan, lock_envelope=lock_env, gentle=trace, preserve_rows=trace)
        apply_query_charger_to_plan(plan, query)
        sync_charger_annotations(plan)
        issues = check_constraints(plan, constraints)
    notes = [n for n in (plan.notes or []) if not str(n).startswith("校验未通过")]
    notes.extend(default_sheet_notes(query))
    notes.extend(issues_as_notes(issues))
    plan.notes = notes[:12]
    return plan, issues
