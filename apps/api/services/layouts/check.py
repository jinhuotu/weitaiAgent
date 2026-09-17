"""确定性工程校验与自动修复：数量/尺寸以 brief + rules 为准，不依赖视觉模型。"""

from __future__ import annotations

from dataclasses import dataclass

from api.services.layouts.brief import (
    LayoutBrief,
    apply_equipment_additions,
    is_electrical_room,
    _replace_stall_mix,
)
from api.services.layouts.pack import (
    _aisle_aabb,
    _eq_box,
    _l_corners_blocked,
    _overlap,
    _point_in_poly,
    expand_stalls,
    plan_gates,
    rect_corners,
)
from api.services.layouts.rules import CAR_AISLE_M, CAR_STALL, TRUCK_AISLE_M, TRUCK_STALL
from api.services.layouts.schema import (
    EvChargingStationPlan,
    ParkingRowSpec,
    site_boundary_m,
    site_is_irregular,
)

_FEEDER = {
    "ring_cabinet",
    "box_transformer",
    "ring_box_transformer",
    "lv_cabinet",
}


@dataclass(frozen=True)
class CheckIssue:
    code: str
    message: str
    blocking: bool = True


def _is_truck(row: ParkingRowSpec) -> bool:
    return float(row.stallLengthM) >= 10 or float(row.stallWidthM) >= 4.6


def _count_mix(plan: EvChargingStationPlan) -> tuple[int, int]:
    trucks = sum(r.stalls for r in plan.parkingRows if _is_truck(r))
    cars = sum(r.stalls for r in plan.parkingRows if not _is_truck(r))
    return trucks, cars


def lock_catalog_sizes(plan: EvChargingStationPlan) -> EvChargingStationPlan:
    """强制目录车位尺寸，忽略模型自由值。"""
    out = plan.model_copy(deep=True)
    tw, tl = TRUCK_STALL
    cw, cl = CAR_STALL
    for row in out.parkingRows:
        if _is_truck(row):
            row.stallWidthM, row.stallLengthM = tw, tl
        else:
            row.stallWidthM, row.stallLengthM = cw, cl
        row.pitchM = None
    return out


def enforce_brief(plan: EvChargingStationPlan, brief: LayoutBrief) -> EvChargingStationPlan:
    """用用户 brief 覆盖桩数/箱变/场地面积；尺寸走目录锁。"""
    out = lock_catalog_sizes(plan)
    if brief.site_w and brief.site_h:
        from api.services.layouts.brief import scale_site_polygon_to_box

        scale_site_polygon_to_box(out, float(brief.site_w), float(brief.site_h))
    elif brief.site_area_m2 is not None:
        from api.services.layouts.brief import scale_site_to_area_m2

        scale_site_to_area_m2(out, brief.site_area_m2)
    if brief.trucks is not None or brief.cars is not None:
        _replace_stall_mix(out, trucks=brief.trucks, cars=brief.cars)
        out = lock_catalog_sizes(out)
    apply_equipment_additions(out, brief)
    return out


def check_plan(plan: EvChargingStationPlan, brief: LayoutBrief) -> list[CheckIssue]:
    issues: list[CheckIssue] = []
    trucks, cars = _count_mix(plan)
    if brief.trucks is not None and trucks != brief.trucks:
        issues.append(
            CheckIssue("truck_count", f"重卡车位 {trucks} 个，用户要求 {brief.trucks} 个")
        )
    if brief.cars is not None and cars != brief.cars:
        issues.append(CheckIssue("car_count", f"轿车车位 {cars} 个，用户要求 {brief.cars} 个"))
    if brief.site_area_m2 is not None:
        from api.services.layouts.brief import _shoelace_area

        poly = [(p.x, p.y) for p in (plan.site.polygon or [])]
        area = (
            _shoelace_area(poly)
            if len(poly) >= 3
            else float(plan.site.widthM) * float(plan.site.heightM)
        )
        if abs(area - float(brief.site_area_m2)) / max(brief.site_area_m2, 1.0) > 0.2:
            issues.append(
                CheckIssue(
                    "site_area",
                    f"场地约 {area:.0f}㎡，草稿/用户要求约 {brief.site_area_m2:g}㎡",
                    blocking=False,
                )
            )

    tw, tl = TRUCK_STALL
    cw, cl = CAR_STALL
    for row in plan.parkingRows:
        if _is_truck(row):
            if abs(row.stallWidthM - tw) > 0.05 or abs(row.stallLengthM - tl) > 0.05:
                issues.append(
                    CheckIssue(
                        "truck_size",
                        f"重卡车位 {row.stallWidthM:g}×{row.stallLengthM:g}，应为 {tw:g}×{tl:g}",
                    )
                )
        elif abs(row.stallWidthM - cw) > 0.05 or abs(row.stallLengthM - cl) > 0.05:
            issues.append(
                CheckIssue(
                    "car_size",
                    f"轿车车位 {row.stallWidthM:g}×{row.stallLengthM:g}，应为 {cw:g}×{cl:g}",
                )
            )

    w, h = plan.site.widthM, plan.site.heightM
    poly = site_boundary_m(plan.site) if site_is_irregular(plan.site) else None
    for row in plan.parkingRows:
        for rect, _ in expand_stalls(row):
            for x, y in rect_corners(rect):
                outside = (
                    (not _point_in_poly(x, y, poly))
                    if poly
                    else (x < -0.05 or y < -0.05 or x > w + 0.05 or y > h + 0.05)
                )
                if outside:
                    issues.append(CheckIssue("redline", "存在车位超出用地红线"))
                    break
            else:
                continue
            break
        else:
            continue
        break

    if brief.transformer_n is not None:
        n_tx = sum(1 for e in plan.equipment if e.type in _FEEDER)
        if n_tx != brief.transformer_n:
            issues.append(
                CheckIssue("transformer_n", f"箱变 {n_tx} 台，用户要求 {brief.transformer_n} 台")
            )
    if brief.transformer_kva is not None:
        kvas = [e.capacityKva for e in plan.equipment if e.type in _FEEDER and e.capacityKva]
        if kvas and abs(float(kvas[0]) - float(brief.transformer_kva)) > 0.5:
            issues.append(
                CheckIssue(
                    "transformer_kva",
                    f"箱变 {kvas[0]:g}kVA，用户要求 {brief.transformer_kva:g}kVA",
                )
            )
    if brief.electrical_room:
        rooms = [b for b in plan.buildings if is_electrical_room(b)]
        if not rooms:
            issues.append(CheckIssue("electrical_room", "用户要求配电室，图上没有"))
        else:
            r = rooms[0].rect
            for eq in plan.equipment:
                if eq.type not in _FEEDER:
                    continue
                inside = (
                    float(r.x) + 0.2 <= float(eq.x) <= float(r.x) + float(r.w) - 0.2
                    and float(r.y) + 0.2 <= float(eq.y) <= float(r.y) + float(r.h) - 0.2
                )
                if not inside:
                    issues.append(
                        CheckIssue("transformer_in_room", "箱变应布置在配电室内，不得占车道")
                    )
                    break
    if brief.host_n is not None:
        n_h = sum(
            1
            for e in plan.equipment
            if e.type == "group_host" or "群冲" in (e.label or "") or "群充" in (e.label or "")
        )
        if n_h != brief.host_n:
            issues.append(
                CheckIssue("host_n", f"群冲主机柜 {n_h} 台，用户要求 {brief.host_n} 台")
            )

    if brief.gate_along == "east" and brief.gate_side in {"south", "north"} and plan_gates(plan):
        span = float(plan.site.widthM)
        g = next((x for x in plan_gates(plan) if x.side == brief.gate_side), None)
        if g is not None:
            mid = float(g.offsetM) + float(g.widthM) / 2.0
            if mid < span * 0.55:
                issues.append(
                    CheckIssue(
                        "gate_along",
                        "出入口应在东南角（南墙偏东），当前在南墙中部",
                        blocking=False,
                    )
                )

    if _l_corners_blocked(plan):
        issues.append(
            CheckIssue(
                "corner_egress",
                "转角车位回转不足，端头车辆无法进出",
                blocking=False,
            )
        )

    # 车道宽度标注：有 aisles 时核对最小值（非阻断，只提示）
    for aisle in plan.aisles:
        need = TRUCK_AISLE_M if "重卡" in (aisle.label or "") else CAR_AISLE_M
        if float(aisle.widthM) + 1e-6 < need * 0.85:
            issues.append(
                CheckIssue(
                    "aisle_width",
                    f"车道 {aisle.id} 宽 {aisle.widthM:g}m，建议≥{need:g}m",
                    blocking=False,
                )
            )

    aisle_boxes = [(a, _aisle_aabb(a)) for a in plan.aisles]
    for eq in plan.equipment:
        eb = _eq_box(float(eq.x), float(eq.y))
        for aisle, box in aisle_boxes:
            if box is not None and _overlap(eb, box, pad=-0.15):
                issues.append(
                    CheckIssue(
                        "aisle_clear",
                        f"{eq.label or eq.type} 压在车道 {aisle.id} 上",
                        blocking=False,
                    )
                )
                break
    return issues


def repair_plan(plan: EvChargingStationPlan, brief: LayoutBrief) -> EvChargingStationPlan:
    """校验前先修：覆盖 brief + 锁尺寸。红线越界仍留给 pack 处理。"""
    return enforce_brief(plan, brief)


def issues_as_notes(issues: list[CheckIssue]) -> list[str]:
    return [f"校验未通过：{item.message}" for item in issues[:6]]
