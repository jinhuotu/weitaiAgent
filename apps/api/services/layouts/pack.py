"""出图前只校正：锁尺寸、桩与车位对应、不越红线/压建筑。不重做布置方案。"""

from __future__ import annotations

import math
from typing import Any

from api.services.layouts.rules import CAR_AISLE_M, CAR_STALL, TRUCK_AISLE_M, TRUCK_STALL
from api.services.layouts.schema import (
    AisleSpec,
    BuildingSpec,
    CableSpec,
    ChargerOnRow,
    EvChargingStationPlan,
    GateSpec,
    ParkingRowSpec,
    PointM,
    RectM,
    TrenchSpec,
    clamp_gate_to_span,
    collect_site_gates,
    set_site_gates,
    site_boundary_m,
    site_is_irregular,
)

_FEEDER_TYPES = {
    "ring_cabinet",
    "box_transformer",
    "ring_box_transformer",
    "lv_cabinet",
}
_CHARGER_EQ = {"dc_320kw", "dc_160kw", "dc_120kw", "ac_14kw"}
Aabb = tuple[float, float, float, float]


def _rot(px: float, py: float, ox: float, oy: float, rad: float) -> tuple[float, float]:
    dx, dy = px - ox, py - oy
    c, s = math.cos(rad), math.sin(rad)
    return ox + dx * c - dy * s, oy + dx * s + dy * c


def rect_corners(rect: RectM) -> list[tuple[float, float]]:
    rad = math.radians(rect.angleDeg)
    pts = [
        (rect.x, rect.y),
        (rect.x + rect.w, rect.y),
        (rect.x + rect.w, rect.y + rect.h),
        (rect.x, rect.y + rect.h),
    ]
    return [_rot(x, y, rect.x, rect.y, rad) for x, y in pts]


def _aabb(pts: list[tuple[float, float]]) -> Aabb:
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def _overlap(a: Aabb, b: Aabb, pad: float = 0.0) -> bool:
    return not (
        a[2] <= b[0] + pad or b[2] <= a[0] + pad or a[3] <= b[1] + pad or b[3] <= a[1] + pad
    )


def _point_in_poly(x: float, y: float, poly: list[tuple[float, float]]) -> bool:
    n = len(poly)
    if n < 3:
        return True
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y):
            xint = (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi
            if x < xint:
                inside = not inside
        j = i
    return inside


def _is_truck_row(row: ParkingRowSpec | dict[str, Any]) -> bool:
    if isinstance(row, dict):
        width, length = float(row["stallWidthM"]), float(row["stallLengthM"])
    else:
        width, length = float(row.stallWidthM), float(row.stallLengthM)
    return length >= 10 and width >= 4.6


def _normalize_row(row: ParkingRowSpec) -> ParkingRowSpec:
    """只锁车位尺寸；斜列角度和沿轴方向跟 JSON。间距不得小于车宽加缝。"""
    data = row.model_dump()
    if _is_truck_row(data):
        data["stallWidthM"], data["stallLengthM"] = TRUCK_STALL
        minimum = max(float(data["stallWidthM"]) + 0.6, 5.5)
    else:
        data["stallWidthM"], data["stallLengthM"] = CAR_STALL
        minimum = max(float(data["stallWidthM"]) + 0.5, 3.2)
    raw = data.get("pitchM")
    if raw is None:
        data["pitchM"] = None
    else:
        try:
            data["pitchM"] = max(minimum, float(raw))
        except (TypeError, ValueError):
            data["pitchM"] = None
    return ParkingRowSpec.model_validate(data)


def row_pitch(row: ParkingRowSpec) -> float:
    width = float(row.stallWidthM)
    if float(row.stallLengthM) >= 10:
        minimum = max(width + 0.6, 5.5)
    else:
        minimum = max(width + 0.5, 3.2)
    if row.pitchM is not None:
        return max(minimum, float(row.pitchM))
    # 对墙竖放：沿墙按车宽+缝排列，不能用 6m 进深当间距（否则重叠）。
    if _wall_end_bays(row):
        return max(minimum, width + 0.8)
    rad = math.radians(row.angleDeg)
    c, s = abs(math.cos(rad)), abs(math.sin(rad))
    if abs(c) < 0.25:
        auto = float(row.stallLengthM) * s + 0.15
    else:
        auto = width / max(c, 0.35) + 0.08
    return max(minimum, auto)


def _wall_end_bays(row: ParkingRowSpec) -> bool:
    """对墙竖放：车长（进深）垂直东西墙，车宽沿墙，车头对桩。"""
    return row.along == "y" and abs(float(row.angleDeg)) < 15


def expand_stalls(row: ParkingRowSpec) -> list[tuple[RectM, int]]:
    step = row_pitch(row)
    dx, dy = (step, 0.0) if row.along != "y" else (0.0, step)
    if _wall_end_bays(row):
        wide, deep = float(row.stallLengthM), float(row.stallWidthM)
        angle = 0.0
    else:
        wide, deep = float(row.stallWidthM), float(row.stallLengthM)
        angle = float(row.angleDeg)
    out: list[tuple[RectM, int]] = []
    for i in range(row.stalls):
        out.append(
            (
                RectM(
                    x=row.origin.x + dx * i,
                    y=row.origin.y + dy * i,
                    w=wide,
                    h=deep,
                    angleDeg=angle,
                ),
                i,
            )
        )
    return out


def charger_outset_m(row: ParkingRowSpec) -> float:
    """桩画在车位轮廓外，避免落在车位矩形内部。"""
    if float(row.stallLengthM) >= 10:
        side = row.charger.side if row.charger else "tail"
        return 0.85 if side == "tail" else 1.0
    side = (row.charger.side if row.charger else "head") or "head"
    # left/right 贴东西墙时略大，避免方块图标压进车位长边
    if side in {"left", "right"}:
        return 0.95
    return 0.9


def charger_point(rect: RectM, side: str, *, outset: float = 0.0) -> tuple[float, float]:
    """桩位：head/tail 沿车位长向；left/right 沿宽向（贴东西墙）。"""
    rad = math.radians(rect.angleDeg)
    s = (side or "head").lower()
    if s == "tail":
        lx, ly = rect.w / 2, -outset
    elif s == "left":
        lx, ly = -outset, rect.h / 2
    elif s == "right":
        lx, ly = rect.w + outset, rect.h / 2
    else:  # head
        lx, ly = rect.w / 2, rect.h + outset
    return _rot(rect.x + lx, rect.y + ly, rect.x, rect.y, rad)


def charger_heads(row: ParkingRowSpec) -> list[tuple[float, float]]:
    if not row.charger or row.charger.type == "none":
        return []
    out_m = charger_outset_m(row)
    return [
        charger_point(rect, row.charger.side, outset=out_m) for rect, _ in expand_stalls(row)
    ]


def _row_aabb(row: ParkingRowSpec) -> Aabb:
    pts = [pt for rect, _ in expand_stalls(row) for pt in rect_corners(rect)]
    return _aabb(pts) if pts else (row.origin.x, row.origin.y, row.origin.x, row.origin.y)


def _poly_aabb(points: list[PointM]) -> Aabb | None:
    if not points:
        return None
    xs = [p.x for p in points]
    ys = [p.y for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def _building_boxes(plan: EvChargingStationPlan) -> list[Aabb]:
    out: list[Aabb] = []
    for b in plan.buildings:
        if b.kind == "demolish":
            continue
        r = b.rect
        out.append((r.x, r.y, r.x + r.w, r.y + r.h))
    return out


def _road_boxes(plan: EvChargingStationPlan) -> list[Aabb]:
    out: list[Aabb] = []
    for road in plan.roads:
        box = _poly_aabb(road.polygon)
        if box is not None:
            out.append(box)
    return out


def _shift_row(row: ParkingRowSpec, dx: float, dy: float) -> None:
    row.origin = PointM(x=row.origin.x + dx, y=row.origin.y + dy)


def _poly_ok(pts: list[tuple[float, float]], poly: list[tuple[float, float]] | None) -> bool:
    if not poly:
        return True
    return all(_point_in_poly(x, y, poly) for x, y in pts)


def _row_inside_site(row: ParkingRowSpec, plan: EvChargingStationPlan) -> bool:
    w, h = float(plan.site.widthM), float(plan.site.heightM)
    poly = site_boundary_m(plan.site) if site_is_irregular(plan.site) else None
    for rect, _ in expand_stalls(row):
        for x, y in rect_corners(rect):
            if x < -0.05 or y < -0.05 or x > w + 0.05 or y > h + 0.05:
                return False
            if poly and not _point_in_poly(x, y, poly):
                return False
    return True


def _nudge_inside_site(row: ParkingRowSpec, plan: EvChargingStationPlan) -> None:
    w, h = float(plan.site.widthM), float(plan.site.heightM)
    poly = site_boundary_m(plan.site) if site_is_irregular(plan.site) else None
    for _ in range(16):
        box = _row_aabb(row)
        dx = dy = 0.0
        if box[0] < 0.4:
            dx += 0.4 - box[0]
        if box[1] < 0.4:
            dy += 0.4 - box[1]
        if box[2] > w - 0.4:
            dx -= box[2] - (w - 0.4)
        if box[3] > h - 0.4:
            dy -= box[3] - (h - 0.4)
        if abs(dx) < 0.02 and abs(dy) < 0.02:
            if poly:
                pts = [pt for rect, _ in expand_stalls(row) for pt in rect_corners(rect)]
                if _poly_ok(pts, poly):
                    return
                _shift_row(row, 0.8, 0.8)
                continue
            return
        _shift_row(row, dx, dy)


def _nudge_off_boxes(
    row: ParkingRowSpec, boxes: list[Aabb], *, site_w: float | None = None
) -> None:
    """压到建筑/道路上时让开；西侧障碍优先往东，避免贴在左红线上。"""
    for _ in range(24):
        box = _row_aabb(row)
        hit = next((b for b in boxes if _overlap(box, b, pad=-0.4)), None)
        if hit is None:
            return
        dx = hit[2] + 1.2 - box[0]
        dy = hit[3] + 1.2 - box[1]
        if site_w and hit[2] < site_w * 0.55 and 0.15 < dx <= 40.0:
            _shift_row(row, dx, 0.0)
            continue
        options: list[tuple[float, float, float]] = []
        if 0.15 < dx <= 36.0:
            options.append((dx, 0.0, dx))
        if 0.15 < dy <= 36.0:
            options.append((0.0, dy, dy))
        if options:
            options.sort(key=lambda item: item[2])
            _shift_row(row, options[0][0], options[0][1])
        elif dy > 0.15:
            _shift_row(row, 0.0, min(dy, 8.0))
        elif dx > 0.15:
            _shift_row(row, min(dx, 8.0), 0.0)
        else:
            _shift_row(row, 1.5, 1.5)


def _unstack_rows(rows: list[ParkingRowSpec]) -> list[ParkingRowSpec]:
    placed: list[ParkingRowSpec] = []
    for row in rows:
        for k in range(24):
            box = _row_aabb(row)
            hit = next((p for p in placed if _overlap(box, _row_aabb(p), pad=-0.3)), None)
            if hit is None:
                break
            if k % 2 == 0:
                step = max(float(row.stallLengthM) + 2.2, (box[3] - box[1]) + 3.5)
                if row.along != "y":
                    _shift_row(row, 0.0, step)
                else:
                    _shift_row(row, step, 0.0)
            else:
                _shift_row(row, max(6.0, (box[2] - box[0]) * 0.55 + 1.5), 0.0)
        placed.append(row)
    return placed


def _row_hits_blocked(row: ParkingRowSpec, plan: EvChargingStationPlan, blocked: list[Aabb]) -> bool:
    if not _row_inside_site(row, plan):
        return True
    box = _row_aabb(row)
    return any(_overlap(box, b, pad=-0.35) for b in blocked)


def _fit_stall_count(row: ParkingRowSpec, plan: EvChargingStationPlan, blocked: list[Aabb]) -> int:
    lo, hi, best = 1, int(row.stalls), 0
    while lo <= hi:
        mid = (lo + hi) // 2
        probe = row.model_copy(deep=True)
        probe.stalls = mid
        if _row_hits_blocked(probe, plan, blocked):
            hi = mid - 1
        else:
            best = mid
            lo = mid + 1
    return max(1, best)


def _wrap_overflow_rows(
    rows: list[ParkingRowSpec], plan: EvChargingStationPlan, blocked: list[Aabb]
) -> list[ParkingRowSpec]:
    """一排沿轴放不下时拆成下一排，不整场重做成南卡北轿模板。"""
    out: list[ParkingRowSpec] = []
    for row in rows:
        remaining = row
        for part in range(12):
            _nudge_off_boxes(remaining, blocked, site_w=float(plan.site.widthM))
            if not _row_hits_blocked(remaining, plan, blocked):
                out.append(remaining)
                break
            nfit = _fit_stall_count(remaining, plan, blocked)
            nfit = min(max(1, nfit), remaining.stalls)
            head = remaining.model_copy(deep=True)
            head.stalls = nfit
            head.id = remaining.id if part == 0 else f"{remaining.id}_{part}"
            out.append(head)
            leftover = remaining.stalls - head.stalls
            if leftover <= 0:
                break
            box = _row_aabb(head)
            remaining = remaining.model_copy(deep=True)
            remaining.id = f"{row.id}_{part + 1}"
            remaining.stalls = leftover
            remaining.origin = PointM(x=head.origin.x, y=min(box[3] + 1.4, float(plan.site.heightM) - 8.0))
            if remaining.origin.y <= head.origin.y + 0.4:
                remaining.origin = PointM(x=box[2] + 1.2, y=head.origin.y)
        else:
            if remaining.stalls > 0 and out[-1].id != remaining.id:
                out.append(remaining)
    return out


def _free_origin_grid(
    plan: EvChargingStationPlan, *, margin: float = 1.2, step: float = 2.0
) -> list[tuple[float, float]]:
    """在红线内采样可放车位排的候选原点（优先南→北、西→东）。"""
    w, h = float(plan.site.widthM), float(plan.site.heightM)
    area = w * h
    if site_is_irregular(plan.site):
        poly0 = site_boundary_m(plan.site)
        if len(poly0) >= 3:
            from api.services.layouts.brief import _shoelace_area

            area = _shoelace_area(poly0) or area
    # 小场地加密网格，提高装下多排的机会
    if area < 400:
        step = min(step, 1.4)
        margin = min(margin, 0.8)
    poly = site_boundary_m(plan.site) if site_is_irregular(plan.site) else None
    if poly and len(poly) >= 3:
        xs = [p[0] for p in poly]
        ys = [p[1] for p in poly]
        x0, x1 = min(xs) + margin, max(xs) - margin
        y0, y1 = min(ys) + margin, max(ys) - margin
    else:
        x0, y0, x1, y1 = margin, margin, w - margin, h - margin
    if x1 <= x0 or y1 <= y0:
        return [(max(margin, w * 0.15), max(margin, h * 0.15))]
    pts: list[tuple[float, float]] = []
    y = y0
    while y <= y1 + 1e-9:
        x = x0
        while x <= x1 + 1e-9:
            if poly is None or _point_in_poly(x, y, poly):
                pts.append((round(x, 2), round(y, 2)))
            x += step
        y += step
    pts.sort(key=lambda p: (p[1], p[0]))
    return pts or [(max(margin, w * 0.15), max(margin, h * 0.15))]


def _best_place_row(
    row: ParkingRowSpec,
    plan: EvChargingStationPlan,
    blocked: list[Aabb],
    origins: list[tuple[float, float]],
) -> ParkingRowSpec | None:
    """在候选点上找能放下最多车位的位置；能全放下则立刻返回。"""
    want = max(1, int(row.stalls))
    best: ParkingRowSpec | None = None
    best_n = 0
    for ox, oy in origins:
        probe = row.model_copy(deep=True)
        probe.origin = PointM(x=ox, y=oy)
        probe.stalls = want
        if not _row_hits_blocked(probe, plan, blocked):
            return probe
        nfit = _fit_stall_count(probe, plan, blocked)
        if nfit > best_n:
            best_n = nfit
            best = probe.model_copy(deep=True)
            best.stalls = nfit
    return best


def _repack_into_site(
    rows: list[ParkingRowSpec],
    plan: EvChargingStationPlan,
    blocked: list[Aabb],
) -> list[ParkingRowSpec]:
    """不规则/越界时：按红线内网格重放各排，装不下则拆排。"""
    origins = _free_origin_grid(plan, step=2.0 if site_is_irregular(plan.site) else 2.5)
    placed: list[ParkingRowSpec] = []
    live_blocked = list(blocked)
    for idx, row in enumerate(rows):
        if not _row_hits_blocked(row, plan, live_blocked):
            placed.append(row)
            live_blocked.append(_row_aabb(row))
            continue
        leftover = max(1, int(row.stalls))
        part = 0
        while leftover > 0 and part < 12:
            probe = row.model_copy(deep=True)
            probe.id = row.id if part == 0 else f"{row.id}_r{part}"
            probe.stalls = leftover
            # 避开已放排：候选原点不得落在已有 AABB 内
            clear_origins = [
                (ox, oy)
                for ox, oy in origins
                if not any(
                    b[0] - 0.5 <= ox <= b[2] + 0.5 and b[1] - 0.5 <= oy <= b[3] + 0.5
                    for b in live_blocked
                )
            ] or origins
            fitted = _best_place_row(probe, plan, live_blocked, clear_origins)
            if fitted is None or fitted.stalls < 1:
                break
            # 与已放排仍重叠则跳过该结果
            box = _row_aabb(fitted)
            if any(_overlap(box, _row_aabb(p), pad=-0.5) for p in placed):
                # 尝试上移一档再试一次
                fitted.origin = PointM(
                    x=fitted.origin.x,
                    y=fitted.origin.y + float(fitted.stallLengthM) + 1.6,
                )
                if _row_hits_blocked(fitted, plan, live_blocked) or any(
                    _overlap(_row_aabb(fitted), _row_aabb(p), pad=-0.5) for p in placed
                ):
                    break
            placed.append(fitted)
            live_blocked.append(_row_aabb(fitted))
            leftover -= int(fitted.stalls)
            part += 1
    return _unstack_rows(placed) if placed else rows


def _nudge_all_rows(rows: list[ParkingRowSpec], plan: EvChargingStationPlan, blocked: list[Aabb]) -> None:
    for row in rows:
        _nudge_off_boxes(row, blocked, site_w=float(plan.site.widthM))
        _nudge_inside_site(row, plan)
        _nudge_off_boxes(row, blocked, site_w=float(plan.site.widthM))


def _relocate_bad_rows_only(
    rows: list[ParkingRowSpec], plan: EvChargingStationPlan, blocked: list[Aabb]
) -> list[ParkingRowSpec]:
    """只重放仍碰撞/越界的排，已合规的排保持原点。"""
    origins = _free_origin_grid(plan, step=2.5)
    placed: list[ParkingRowSpec] = []
    live = list(blocked)
    for row in rows:
        if not _row_hits_blocked(row, plan, live):
            placed.append(row)
            live.append(_row_aabb(row))
            continue
        clear = [
            (ox, oy)
            for ox, oy in origins
            if not any(
                b[0] - 0.5 <= ox <= b[2] + 0.5 and b[1] - 0.5 <= oy <= b[3] + 0.5 for b in live
            )
        ] or origins
        fitted = _best_place_row(row, plan, live, clear)
        if fitted is None:
            placed.append(row)
            continue
        placed.append(fitted)
        live.append(_row_aabb(fitted))
    return placed


def pack_parking(plan: EvChargingStationPlan, *, gentle: bool = False) -> None:
    """锁尺寸后避让建筑/道路。

    gentle=True（修订模式）：只轻推与拆溢出排，不对整场做网格重装；
    仅当个别排仍越界/碰撞时，只重放坏排。
    """
    rows = [_normalize_row(r) for r in plan.parkingRows]
    if not rows:
        return
    blocked = _building_boxes(plan) + _road_boxes(plan)
    if gentle:
        _nudge_all_rows(rows, plan, blocked)
        want = sum(r.stalls for r in rows)
        # 仅对仍越界的排尝试拆排，避免无故改动已贴墙/已定好的排
        kept: list[ParkingRowSpec] = []
        for row in rows:
            if _row_hits_blocked(row, plan, blocked):
                kept.extend(_wrap_overflow_rows([row], plan, blocked))
            else:
                kept.append(row)
        rows = kept
        if any(_row_hits_blocked(r, plan, blocked) for r in rows):
            rows = _relocate_bad_rows_only(rows, plan, blocked)
        if sum(r.stalls for r in rows) < want and rows:
            pass
        _nudge_all_rows(rows, plan, blocked)
        plan.parkingRows = rows
        return

    rows = _unstack_rows(rows)
    _nudge_all_rows(rows, plan, blocked)
    want = sum(r.stalls for r in rows)
    rows = _wrap_overflow_rows(rows, plan, blocked)
    still_bad = any(_row_hits_blocked(r, plan, blocked) for r in rows) or (
        site_is_irregular(plan.site) and any(not _row_inside_site(r, plan) for r in rows)
    )
    got = sum(r.stalls for r in rows)
    # 禁止把装不下的车位硬加回同一排（会造成桩体重叠）
    if still_bad or got < want:
        rows = _repack_into_site(rows, plan, blocked)
        got = sum(r.stalls for r in rows)
    if got < want and rows:
        # 仍不足：按可容纳数保留，不在同一 origin 上虚增 stalls
        pass
    _nudge_all_rows(rows, plan, blocked)
    plan.parkingRows = rows


def _row_bind_order(rows: list[ParkingRowSpec]) -> list[ParkingRowSpec]:
    """重编号时保住「桩号 ↔ 那一排」：已有不重复编号则按桩号；否则北→南、西→东。

    只按 parkingRows 数组顺序从 1 重编时，27–50 的 160kW 会被编到后面一排，
    文案仍写 27–50 已改，图纸上 27–50 还是 320kW。
    """
    starts = [int(r.charger.startNo) if r.charger else 1 for r in rows]
    if len(rows) >= 2 and len(set(starts)) == len(starts) and max(starts) > 1:
        return sorted(rows, key=lambda r: int(r.charger.startNo) if r.charger else 1)
    return sorted(
        rows,
        key=lambda r: (-round(float(r.origin.y), 2), round(float(r.origin.x), 2)),
    )


def _bind_chargers_to_stalls(plan: EvChargingStationPlan) -> None:
    """只按排位顺序钉桩号，不改、不发明功率。缺桩型保持 none，由用户语句或条件表后涂。"""
    plan.parkingRows = _row_bind_order(list(plan.parkingRows))
    start = 1
    for row in plan.parkingRows:
        charger = row.charger
        if charger is None:
            row.charger = ChargerOnRow(type="none", startNo=start, side="head")
        else:
            row.charger = charger.model_copy(update={"startNo": start})
        start += row.stalls
    plan.equipment = [eq for eq in plan.equipment if eq.type not in _CHARGER_EQ]


def _interior_overlap(a: Aabb, b: Aabb, *, min_ov: float = 0.8) -> bool:
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    return (x1 - x0) > min_ov and (y1 - y0) > min_ov


def _drop_copied_yard_roads(plan: EvChargingStationPlan) -> None:
    """案例里的整块「内部道路」不是本轮车道；用户写的细长「路」保留。"""
    buildings = _building_boxes(plan)
    kept = []
    for road in plan.roads:
        box = _poly_aabb(road.polygon)
        if box is None:
            continue
        lab = str(road.label or "")
        if any(tok in lab for tok in ("内部", "回车", "通行区", "主通道")):
            continue
        if lab != "路" and any(_interior_overlap(box, b) for b in buildings):
            continue
        kept.append(road)
    plan.roads = kept


def _clamp_one_gate(plan: EvChargingStationPlan, gate: GateSpec) -> GateSpec:
    w, h = plan.site.widthM, plan.site.heightM
    span = w if gate.side in {"north", "south"} else h
    offset, width = float(gate.offsetM), float(gate.widthM)
    # 门仍在这面墙上就原样保留，只在开口会落到墙外时才收进边长。
    if offset >= -0.05 and offset + width <= float(span) + 0.05 and width > 0.4:
        label = str(gate.label or "").strip() or "出入口"
        return gate.model_copy(update={"label": label})
    offset, width = clamp_gate_to_span(gate.offsetM, gate.widthM, span)
    label = str(gate.label or "").strip() or "出入口"
    return gate.model_copy(update={"offsetM": offset, "widthM": width, "label": label})


def _default_gate(plan: EvChargingStationPlan) -> GateSpec:
    w = plan.site.widthM
    width = min(10.0, max(8.0, 0.12 * w))
    offset = max(0.0, (w - width) / 2)
    offset, width = clamp_gate_to_span(offset, width, w)
    return GateSpec(side="south", offsetM=offset, widthM=width, label="出入口")


def _snapshot_gates(
    plan: EvChargingStationPlan,
) -> tuple[float, float, list[GateSpec]]:
    return (
        float(plan.site.widthM),
        float(plan.site.heightM),
        [g.model_copy(deep=True) for g in collect_site_gates(plan.site)],
    )


def _restore_gates(
    plan: EvChargingStationPlan, saved: tuple[float, float, list[GateSpec]]
) -> None:
    """出入口是场地基本信息：不得为靠墙布桩改门。场地放大时只保持角位，不居中。"""
    old_w, old_h, gates = saved
    if not gates:
        return
    from api.services.layouts.brief import gate_offset_on_span, infer_gate_along

    fitted: list[GateSpec] = []
    for gate in gates:
        old_span = old_w if gate.side in {"north", "south"} else old_h
        new_span = (
            float(plan.site.widthM)
            if gate.side in {"north", "south"}
            else float(plan.site.heightM)
        )
        if abs(new_span - old_span) < 0.05:
            fitted.append(_clamp_one_gate(plan, gate))
            continue
        along = infer_gate_along(
            side=str(gate.side),
            offset=float(gate.offsetM),
            width=float(gate.widthM),
            span=old_span,
        )
        width = float(gate.widthM)
        if along in {"east", "west", "north", "south"}:
            offset = gate_offset_on_span(
                along, side=str(gate.side), span=new_span, width=width
            )
        elif old_span > 0.5:
            mid_frac = (float(gate.offsetM) + width / 2.0) / old_span
            width = min(width, max(0.5, new_span - 0.5))
            offset = mid_frac * new_span - width / 2.0
        else:
            fitted.append(_clamp_one_gate(plan, gate))
            continue
        offset, width = clamp_gate_to_span(offset, width, new_span)
        fitted.append(gate.model_copy(update={"offsetM": offset, "widthM": width}))
    set_site_gates(plan.site, [_clamp_one_gate(plan, g) for g in fitted])


def _align_gate(plan: EvChargingStationPlan) -> None:
    gates = [_clamp_one_gate(plan, g) for g in collect_site_gates(plan.site)]
    if not gates:
        gates = [_default_gate(plan)]
    set_site_gates(plan.site, gates)


def plan_gates(plan: EvChargingStationPlan) -> list[GateSpec]:
    return collect_site_gates(plan.site)


def _strip_spurious_gate_labels(plan: EvChargingStationPlan) -> None:
    for road in plan.roads:
        lab = str(road.label or "")
        if any(tok in lab for tok in ("出入口", "入口", "大门")):
            road.label = ""
    for g in plan.greenery:
        lab = str(g.label or "")
        if any(tok in lab for tok in ("出入口", "入口", "大门")):
            g.label = "绿化"


def _infer_row_wall(
    plan: EvChargingStationPlan, row: ParkingRowSpec, *, margin: float = 2.8
) -> str | None:
    """若车位排贴某侧围墙/红线，返回 north|south|east|west。"""
    box = _row_aabb(row)
    w, h = float(plan.site.widthM), float(plan.site.heightM)
    dists = {
        "west": box[0],
        "east": w - box[2],
        "south": box[1],
        "north": h - box[3],
    }
    side, dist = min(dists.items(), key=lambda kv: kv[1])
    if dist > margin:
        return None
    along = str(row.along or "")
    if along == "y":
        we = "west" if dists["west"] <= dists["east"] else "east"
        if dists[we] <= margin:
            return we
    if along == "x":
        ns = "south" if dists["south"] <= dists["north"] else "north"
        if dists[ns] <= margin:
            return ns
    return side


def charger_side_for_wall(wall: str) -> str:
    """桩在车头（贴墙短边），禁止贴在车身长边。东西墙用 left/right 表示西/东端车头。"""
    return {
        "north": "head",
        "south": "tail",
        "west": "left",
        "east": "right",
    }.get(wall, "head")


def _gate_sides(plan: EvChargingStationPlan) -> set[str]:
    return {str(g.side) for g in collect_site_gates(plan.site)}


def _compact_yard(plan: EvChargingStationPlan) -> bool:
    """约 1000㎡、边长不足约 42m：必须先留大门通道，不能再东西对贴把路挤歪。"""
    w, h = float(plan.site.widthM), float(plan.site.heightM)
    return min(w, h) < 42.0 or (w * h) < 1800.0


def _has_yard_obstacles(plan: EvChargingStationPlan) -> bool:
    """厂房、办公楼、现状路：走原贴墙逻辑，不要按空场转角门重排。"""
    from api.services.layouts.brief import is_electrical_room

    if getattr(plan, "roads", None):
        return True
    if site_is_irregular(plan.site):
        return True
    return any(
        getattr(b, "kind", "") != "demolish" and not is_electrical_room(b)
        for b in plan.buildings
    )


def _circulation_first(plan: EvChargingStationPlan) -> bool:
    """小场地 + 大门贴侧墙：先贯穿留路，再在其余空白地贴车/设备。"""
    return bool(
        _compact_yard(plan)
        and _gate_flush_wall(plan)
        and not _has_yard_obstacles(plan)
    )


def _gate_flush_wall(plan: EvChargingStationPlan) -> str | None:
    """大门贴在哪条侧墙（东南门→east，北门偏东→east）。这条墙是进场车道，小场地不要再贴车。"""
    gates = collect_site_gates(plan.site)
    if not gates:
        return None
    g = gates[0]
    w, h = float(plan.site.widthM), float(plan.site.heightM)
    mid = float(g.offsetM) + float(g.widthM) / 2.0
    if str(g.side) in {"south", "north"}:
        if mid >= w * 0.55:
            return "east"
        if mid <= w * 0.45:
            return "west"
        return None
    if mid >= h * 0.55:
        return "north"
    if mid <= h * 0.45:
        return "south"
    return None


def _gate_entry_walls(plan: EvChargingStationPlan) -> set[str]:
    sides = {str(g.side) for g in collect_site_gates(plan.site)}
    flush = _gate_flush_wall(plan)
    if flush:
        sides.add(flush)
    return sides


def _opposing_layout_walls(plan: EvChargingStationPlan) -> tuple[str, str]:
    """优先左右两侧（西+东）贴墙；仅当左右有出入口时才改贴上下。出入口边不贴车。

    小场地：大门所在边及其贴墙侧都留作进场车道，车位改贴其余两面（可相邻，如西+南）。
    """
    gates = _gate_sides(plan)
    we, ns = ("west", "east"), ("north", "south")
    entry = _gate_entry_walls(plan) if _circulation_first(plan) else set(gates)

    def _free(pair: tuple[str, str]) -> bool:
        return all(side not in entry for side in pair)

    if _free(we):
        return we
    if _free(ns):
        return ns
    if _circulation_first(plan):
        rest = [s for s in ("west", "south", "east", "north") if s not in entry]
        if len(rest) >= 2:
            return (rest[0], rest[1])
    we_hit = sum(1 for side in we if side in entry)
    ns_hit = sum(1 for side in ns if side in entry)
    if we_hit != ns_hit:
        return we if we_hit < ns_hit else ns
    return we


def _gate_spine_axis(plan: EvChargingStationPlan) -> tuple[str, float, float] | None:
    """大门轴线走廊：('x', 中心x, 半宽) 竖向，或 ('y', 中心y, 半宽) 横向。"""
    gates = collect_site_gates(plan.site)
    if not gates:
        return None
    gate = gates[0]
    w, h = float(plan.site.widthM), float(plan.site.heightM)
    need = TRUCK_AISLE_M if any(_is_truck_row(r) for r in plan.parkingRows) else CAR_AISLE_M
    half = need * 0.5
    mid = float(gate.offsetM) + float(gate.widthM) / 2.0
    if str(gate.side) in {"south", "north"}:
        cx = min(max(mid, half + 0.4), w - half - 0.4)
        return ("x", cx, half)
    cy = min(max(mid, half + 0.4), h - half - 0.4)
    return ("y", cy, half)


def _gate_spine_keepouts(plan: EvChargingStationPlan) -> list[Aabb]:
    """从大门贯穿场地的行车走廊。转角大门的小场地先占路，车位和设备不得落入。"""
    if not _circulation_first(plan):
        return []
    axis = _gate_spine_axis(plan)
    if axis is None:
        return []
    w, h = float(plan.site.widthM), float(plan.site.heightM)
    kind, mid, half = axis
    if kind == "x":
        return [(mid - half, 0.4, mid + half, h - 0.4)]
    return [(0.4, mid - half, w - 0.4, mid + half)]


def _usable_perimeter_walls(plan: EvChargingStationPlan, *, n_rows: int = 2) -> list[str]:
    """无出入口的对侧边循环使用；两排及以上不混用相邻墙。"""
    a, b = _opposing_layout_walls(plan)
    if n_rows <= 1:
        return [a]
    return [a, b]


def _flush_row_to_wall(
    row: ParkingRowSpec,
    plan: EvChargingStationPlan,
    side: str,
    *,
    margin: float = 0.8,
) -> None:
    """把一排车位对墙竖放：车长（进深）垂直围墙，车头贴墙对桩，沿墙按车宽排列。"""
    if abs(float(row.angleDeg or 0)) > 5:
        return
    if side in {"west", "east"}:
        row.along = "y"
        row.angleDeg = 0.0
    else:
        row.along = "x"
        row.angleDeg = 0.0
    row.pitchM = float(row.stallWidthM) + 0.8
    if row.charger and row.charger.type != "none":
        row.charger = row.charger.model_copy(update={"side": charger_side_for_wall(side)})
    w, h = float(plan.site.widthM), float(plan.site.heightM)
    pitch = row_pitch(row)
    bay = float(row.stallWidthM)
    depth = float(row.stallLengthM)
    span = pitch * max(0, int(row.stalls) - 1) + bay
    lo, hi = _along_available(plan, side, margin=margin)
    avail = max(0.0, hi - lo)
    along0 = lo if avail <= span + 0.2 else lo + max(0.0, (avail - span) * 0.5)
    along0 = max(lo, along0)
    if side == "west":
        row.origin = PointM(x=margin, y=along0)
    elif side == "east":
        row.origin = PointM(
            x=max(margin, w - depth - margin),
            y=along0,
        )
    elif side == "north":
        row.origin = PointM(
            x=along0,
            y=max(margin, h - depth - margin),
        )
    else:
        row.origin = PointM(x=along0, y=margin)
    north_limit = h - margin
    if side == "north":
        for b in plan.buildings:
            if b.kind == "demolish":
                continue
            south = float(b.rect.y)
            if south > h * 0.35:
                north_limit = min(north_limit, south - margin)
    for _ in range(48):
        box = _row_aabb(row)
        if side == "north":
            gap = north_limit - box[3]
            if gap <= 0.12:
                break
            _shift_row(row, 0.0, min(gap, 1.8))
        elif side == "south":
            gap = box[1] - margin
            if gap <= 0.12:
                break
            _shift_row(row, 0.0, -min(gap, 1.8))
        elif side == "east":
            gap = (w - margin) - box[2]
            if gap <= 0.12:
                break
            _shift_row(row, min(gap, 1.8), 0.0)
        else:
            gap = box[0] - margin
            if gap <= 0.12:
                break
            _shift_row(row, -min(gap, 1.8), 0.0)


def _ensure_opposing_car_rows(plan: EvChargingStationPlan) -> None:
    """单排且多于 4 台时拆成两排贴对侧围墙。已有两排及以上不再合并。"""
    cars = [r for r in plan.parkingRows if not _is_truck_row(r)]
    trucks = [r for r in plan.parkingRows if _is_truck_row(r)]
    n = sum(int(r.stalls) for r in cars)
    if n <= 4 or len(plan.parkingRows) >= 2:
        return
    sample = cars[0]
    left = (n + 1) // 2
    right = n - left

    def _mk(rid: str, stalls: int, start: int) -> ParkingRowSpec:
        row = sample.model_copy(deep=True)
        row.id = rid
        row.stalls = stalls
        if row.charger:
            row.charger = row.charger.model_copy(update={"startNo": start})
        return row

    plan.parkingRows = [_mk("cars_a", left, 1), _mk("cars_b", right, left + 1), *trucks]


def _along_available(
    plan: EvChargingStationPlan, side: str, *, margin: float = 0.8
) -> tuple[float, float]:
    """该墙可贴车的沿墙区间，扣掉本墙大门，东南门还扣东墙南端进场段。"""
    w, h = float(plan.site.widthM), float(plan.site.heightM)
    if side in {"west", "east"}:
        lo, hi = margin, h - margin
    else:
        lo, hi = margin, w - margin
    pad = 0.6
    for gate in collect_site_gates(plan.site):
        gs = str(gate.side)
        g0 = float(gate.offsetM) - pad
        g1 = float(gate.offsetM) + float(gate.widthM) + pad
        if gs == side:
            left = max(0.0, min(hi, g0) - lo)
            right = max(0.0, hi - max(lo, g1))
            if left >= right:
                hi = min(hi, g0)
            else:
                lo = max(lo, g1)
        elif side == "east" and gs == "south" and g1 >= w - 8.0:
            lo = max(lo, min(CAR_AISLE_M, h * 0.25))
        elif side == "west" and gs == "south" and g0 <= 8.0:
            lo = max(lo, min(CAR_AISLE_M, h * 0.25))
        elif side == "east" and gs == "north" and g1 >= w - 8.0:
            hi = min(hi, h - min(CAR_AISLE_M, h * 0.25))
        elif side == "west" and gs == "north" and g0 <= 8.0:
            hi = min(hi, h - min(CAR_AISLE_M, h * 0.25))
    if _compact_yard(plan):
        for spine in _gate_spine_keepouts(plan):
            vertical = (spine[3] - spine[1]) >= (spine[2] - spine[0])
            if vertical and side in {"south", "north"}:
                left = spine[0] - 0.5 - lo
                right = hi - (spine[2] + 0.5)
                if left >= right:
                    hi = min(hi, spine[0] - 0.5)
                else:
                    lo = max(lo, spine[2] + 0.5)
            elif (not vertical) and side in {"west", "east"}:
                below = spine[1] - 0.5 - lo
                above = hi - (spine[3] + 0.5)
                if below >= above:
                    hi = min(hi, spine[1] - 0.5)
                else:
                    lo = max(lo, spine[3] + 0.5)
    from api.services.layouts.brief import is_electrical_room

    inset = 18.0 if any(_is_truck_row(r) for r in plan.parkingRows) else 8.0
    if site_is_irregular(plan.site) or inset <= 0:
        return lo, hi
    for b in plan.buildings:
        if getattr(b, "kind", "") == "demolish" or is_electrical_room(b):
            continue
        r = b.rect
        bx0, by0 = float(r.x), float(r.y)
        bx1, by1 = bx0 + float(r.w), by0 + float(r.h)
        a0 = a1 = 0.0
        blocks = False
        gap = {"north": h - by1, "south": by0, "west": bx0, "east": w - bx1}[side]
        # 贴在西墙的厂房：南北墙只从厂房以东起铺，场地被拔高后仍要让开。
        side_block = (side in {"north", "south"} and (bx0 <= 1.5 or bx1 >= w - 1.5)) or (
            side in {"west", "east"} and (by0 <= 1.5 or by1 >= h - 1.5)
        )
        if gap < inset or side_block:
            blocks = True
            if side in {"north", "south"}:
                a0, a1 = bx0 - 0.5, bx1 + 0.5
            else:
                a0, a1 = by0 - 0.5, by1 + 0.5
        if not blocks:
            continue
        left = max(0.0, min(hi, a0) - lo)
        right = max(0.0, hi - max(lo, a1))
        if left >= right:
            hi = min(hi, a0)
        else:
            lo = max(lo, a1)
    for box in _road_boxes(plan):
        bx0, by0, bx1, by1 = box
        a0 = a1 = 0.0
        blocks = False
        gap = {"north": h - by1, "south": by0, "west": bx0, "east": w - bx1}[side]
        side_block = (side in {"north", "south"} and (bx0 <= 1.5 or bx1 >= w - 1.5)) or (
            side in {"west", "east"} and (by0 <= 1.5 or by1 >= h - 1.5)
        )
        if gap < inset or side_block:
            blocks = True
            if side in {"north", "south"}:
                a0, a1 = bx0 - 0.5, bx1 + 0.5
            else:
                a0, a1 = by0 - 0.5, by1 + 0.5
        if not blocks:
            continue
        left = max(0.0, min(hi, a0) - lo)
        right = max(0.0, hi - max(lo, a1))
        if left >= right:
            hi = min(hi, a0)
        else:
            lo = max(lo, a1)
    if hi < lo + 0.5:
        if _has_yard_obstacles(plan):
            return lo, hi
        if side in {"west", "east"}:
            return margin, h - margin
        return margin, w - margin
    return lo, hi


def _stalls_fit_length(length: float, *, bay: float, pitch: float) -> int:
    if length < bay - 1e-9:
        return 0
    return 1 + int((length - bay + 1e-9) / pitch)


def _wall_fill_order(plan: EvChargingStationPlan) -> list[str]:
    if _circulation_first(plan):
        a, b = _opposing_layout_walls(plan)
        entry = _gate_entry_walls(plan)
        rest = [
            s
            for s in ("west", "south", "east", "north")
            if s not in {a, b} and s not in entry
        ]
        return [a, b, *rest]
    w, h = float(plan.site.widthM), float(plan.site.heightM)
    if w + 0.05 >= h:
        return ["north", "south", "west", "east"]
    return ["west", "east", "north", "south"]


def _wall_along_window(
    plan: EvChargingStationPlan,
    wall: str,
    *,
    depth: float,
    used: set[str],
) -> tuple[float, float]:
    lo, hi = _along_available(plan, wall)
    aisle = TRUCK_AISLE_M if depth >= 10 else CAR_AISLE_M
    corner = max(depth + 0.5, aisle)
    if wall in {"west", "east"}:
        if "south" in used:
            lo += corner
        if "north" in used:
            hi -= corner
    else:
        if "west" in used:
            lo += corner
        if "east" in used:
            hi -= corner
    return lo, hi


def _wall_capacity(
    plan: EvChargingStationPlan,
    wall: str,
    *,
    bay: float,
    pitch: float,
    depth: float,
    used: set[str],
) -> int:
    lo, hi = _wall_along_window(plan, wall, depth=depth, used=used)
    return _stalls_fit_length(max(0.0, hi - lo), bay=bay, pitch=pitch)


def _l_corners_blocked(plan: EvChargingStationPlan, *, aisle: float | None = None) -> bool:
    """转角两排都伸进角落时，端头车开口对撞，回转不足。"""
    need = float(aisle if aisle is not None else CAR_AISLE_M)
    by_wall: dict[str, Aabb] = {}
    for row in plan.parkingRows:
        wall = _infer_row_wall(plan, row)
        if not wall:
            continue
        box = _row_aabb(row)
        prev = by_wall.get(wall)
        by_wall[wall] = box if prev is None else _merge_boxes([prev, box])
    checks = (
        ("north", "west", lambda n, w: (n[1] - w[3], n[0] - w[2])),
        ("north", "east", lambda n, e: (n[1] - e[3], e[0] - n[2])),
        ("south", "west", lambda s, w: (w[1] - s[3], s[0] - w[2])),
        ("south", "east", lambda s, e: (e[1] - s[3], e[0] - s[2])),
    )
    for a, b, gaps in checks:
        if a not in by_wall or b not in by_wall:
            continue
        ns_gap, we_gap = gaps(by_wall[a], by_wall[b])
        if ns_gap < need - 0.15 and we_gap < need - 0.15:
            return True
    return False


def _charger_type_by_stall(plan: EvChargingStationPlan) -> dict[int, str]:
    mapping: dict[int, str] = {}
    for row in plan.parkingRows:
        ch = row.charger
        if not ch or ch.type == "none":
            continue
        start = int(ch.startNo or 1)
        for i in range(int(row.stalls)):
            mapping[start + i] = str(ch.type)
    return mapping


def _shift_row_origin(row: ParkingRowSpec, skip: int) -> None:
    if skip <= 0:
        return
    pitch = row_pitch(row)
    if row.along == "y":
        row.origin = PointM(x=row.origin.x, y=row.origin.y + pitch * skip)
    else:
        row.origin = PointM(x=row.origin.x + pitch * skip, y=row.origin.y)


def _stamp_charger_types(
    rows: list[ParkingRowSpec], type_by_no: dict[int, str]
) -> list[ParkingRowSpec]:
    """重装排位后按桩号写回功率；一排跨两种桩型时切开，避免图纸仍整排 320kW。"""
    if not type_by_no:
        return rows
    stamped: list[ParkingRowSpec] = []
    for row in rows:
        start = int(row.charger.startNo) if row.charger else 1
        n = int(row.stalls)
        kinds = [type_by_no.get(start + i) for i in range(n)]
        uniq = {k for k in kinds if k}
        if len(uniq) <= 1:
            kind = next(iter(uniq), None)
            if kind and row.charger:
                row.charger.type = kind  # type: ignore[assignment]
                if kind in _CHARGER_LABELS:
                    row.labelPrefix = _CHARGER_LABELS[kind]
            stamped.append(row)
            continue
        i = 0
        while i < n:
            kind = kinds[i] or (str(row.charger.type) if row.charger else "none")
            j = i + 1
            while j < n and (kinds[j] or kind) == kind:
                j += 1
            part = row.model_copy(deep=True)
            part.id = f"{row.id}_{i}"
            part.stalls = j - i
            _shift_row_origin(part, i)
            if part.charger:
                part.charger = part.charger.model_copy(
                    update={"startNo": start + i, "type": kind}
                )
            if kind in _CHARGER_LABELS:
                part.labelPrefix = _CHARGER_LABELS[kind]
            stamped.append(part)
            i = j
    return stamped


def _fit_cars_into_envelope(plan: EvChargingStationPlan) -> bool:
    """场地长宽锁定时：先贴墙；转角倒不出则只保留长边墙，余量换行并留车道。"""
    cars = [r for r in plan.parkingRows if not _is_truck_row(r)]
    trucks = [r for r in plan.parkingRows if _is_truck_row(r)]
    n = sum(int(r.stalls) for r in cars)
    if n <= 0 or not cars:
        return False
    type_by_no = _charger_type_by_stall(plan)
    sample = cars[0]
    bay = float(sample.stallWidthM)
    depth = float(sample.stallLengthM)
    pitch = bay + 0.8
    if depth >= 10:
        pitch = max(pitch, bay + 0.6, 5.5)
    aisle = TRUCK_AISLE_M if depth >= 10 else CAR_AISLE_M
    order = _wall_fill_order(plan)

    def _mk(rid: str, stalls: int, no: int) -> ParkingRowSpec:
        row = sample.model_copy(deep=True)
        row.id = rid
        row.stalls = max(1, int(stalls))
        kinds = [type_by_no.get(no + i) for i in range(max(1, int(stalls)))]
        kind = next((k for k in kinds if k), None)
        if row.charger:
            update: dict[str, object] = {"startNo": no}
            if kind:
                update["type"] = kind
            row.charger = row.charger.model_copy(update=update)
            if kind in _CHARGER_LABELS:
                row.labelPrefix = _CHARGER_LABELS[kind]
        return row

    def _fill(walls: list[str]) -> tuple[list[ParkingRowSpec], int, int]:
        used: set[str] = set()
        rows: list[ParkingRowSpec] = []
        left = n
        start = 1
        for wall in walls:
            if left <= 0:
                break
            cap = _wall_capacity(plan, wall, bay=bay, pitch=pitch, depth=depth, used=used)
            take = min(left, cap)
            if take <= 0:
                continue
            row = _mk(f"cars_{wall}", take, start)
            lo, hi = _wall_along_window(plan, wall, depth=depth, used=used)
            _flush_row_to_wall(row, plan, wall)
            _crop_row_to_along(row, wall, lo=lo, hi=hi)
            _clear_row_from_siblings(row, wall, rows, aisle=aisle)
            _crop_row_to_along(row, wall, lo=lo, hi=hi)
            if int(row.stalls) <= 0:
                continue
            rows.append(row)
            used.add(wall)
            start += int(row.stalls)
            left -= int(row.stalls)
        return rows, left, start

    rows, left, start = _fill(order)
    probe = plan.model_copy(deep=True)
    probe.parkingRows = rows
    if rows and _l_corners_blocked(probe, aisle=aisle):
        rows, left, start = _fill(order[:2])
    if left > 0:
        wraps = _place_wrap_rows(
            plan, sample, leftover=left, start=start, wall_rows=rows, aisle=aisle
        )
        placed = sum(int(r.stalls) for r in wraps)
        rows.extend(wraps)
        start += placed
        left -= placed
    if not rows:
        return False
    rows = _stamp_charger_types(rows, type_by_no)
    plan.parkingRows = rows + trucks
    return True


def _crop_row_to_along(
    row: ParkingRowSpec, wall: str, *, lo: float, hi: float
) -> None:
    """把沿墙排裁进 [lo, hi]，缩短端头而不是整排平移出界。"""
    pitch = row_pitch(row)
    bay = float(row.stallWidthM)
    if pitch <= 1e-6 or int(row.stalls) <= 0:
        return
    box = _row_aabb(row)
    if wall in {"west", "east"}:
        a0, a1 = box[1], box[3]
        axis = "y"
    else:
        a0, a1 = box[0], box[2]
        axis = "x"
    if a0 >= lo - 1e-6 and a1 <= hi + 1e-6:
        return
    if a0 < lo:
        drop = int(math.ceil((lo - a0 - 1e-6) / pitch))
        drop = min(drop, max(0, int(row.stalls) - 1))
        row.stalls = int(row.stalls) - drop
        if axis == "x":
            _shift_row(row, drop * pitch, 0.0)
        else:
            _shift_row(row, 0.0, drop * pitch)
        box = _row_aabb(row)
        a1 = box[3] if axis == "y" else box[2]
    if a1 > hi and int(row.stalls) > 1:
        extra = a1 - hi
        drop = int(math.ceil((extra - 1e-6) / pitch))
        row.stalls = max(1, int(row.stalls) - drop)


def _clear_row_from_siblings(
    row: ParkingRowSpec,
    wall: str,
    siblings: list[ParkingRowSpec],
    *,
    aisle: float,
) -> None:
    """贴墙排躲开已放的垂直墙排，开口侧至少留回转车道。"""
    box = _row_aabb(row)
    for other in siblings:
        oid = str(other.id or "")
        ob = _row_aabb(other)
        if wall in {"west", "east"} and oid.endswith("_south"):
            need = ob[3] + aisle
            if box[1] < need - 1e-6:
                _shift_row(row, 0.0, need - box[1])
                box = _row_aabb(row)
        elif wall in {"west", "east"} and oid.endswith("_north"):
            need = ob[1] - aisle
            if box[3] > need + 1e-6:
                _shift_row(row, 0.0, need - box[3])
                box = _row_aabb(row)
        elif wall in {"north", "south"} and oid.endswith("_west"):
            need = ob[2] + aisle
            if box[0] < need - 1e-6:
                _shift_row(row, need - box[0], 0.0)
                box = _row_aabb(row)
        elif wall in {"north", "south"} and oid.endswith("_east"):
            need = ob[0] - aisle
            if box[2] > need + 1e-6:
                _shift_row(row, need - box[2], 0.0)
                box = _row_aabb(row)


def _inner_courtyard(
    plan: EvChargingStationPlan,
    wall_rows: list[ParkingRowSpec],
    *,
    aisle: float,
) -> tuple[float, float, float, float]:
    """贴墙车位内侧可居中布岛的矩形，四周已扣回转车道。"""
    w, h = float(plan.site.widthM), float(plan.site.heightM)
    x0, y0, x1, y1 = aisle, aisle, w - aisle, h - aisle
    for row in wall_rows:
        wall = str(row.id or "").rsplit("_", 1)[-1]
        if wall not in {"west", "east", "north", "south"}:
            wall = _infer_row_wall(plan, row) or ""
        box = _row_aabb(row)
        if wall == "west":
            x0 = max(x0, box[2] + aisle)
        elif wall == "east":
            x1 = min(x1, box[0] - aisle)
        elif wall == "south":
            y0 = max(y0, box[3] + aisle)
        elif wall == "north":
            y1 = min(y1, box[1] - aisle)
    for keep in _gate_throat_keepouts(plan):
        if keep[1] <= aisle + 0.5 and keep[3] >= y0 - 0.5:
            x0 = max(x0, min(x1 - 1.0, keep[2] + 0.3)) if keep[0] <= 1.0 else x0
            x1 = min(x1, max(x0 + 1.0, keep[0] - 0.3)) if keep[2] >= w - 1.0 else x1
    for gate in collect_site_gates(plan.site):
        mid = float(gate.offsetM) + float(gate.widthM) / 2.0
        if str(gate.side) in {"south", "north"}:
            if mid >= w * 0.55:
                x1 = min(x1, float(gate.offsetM) - 0.3)
            elif mid <= w * 0.45:
                x0 = max(x0, float(gate.offsetM) + float(gate.widthM) + 0.3)
        elif str(gate.side) == "east":
            x1 = min(x1, w - max(aisle, float(gate.widthM)))
        elif str(gate.side) == "west":
            x0 = max(x0, max(aisle, float(gate.widthM)))
    for spine in _gate_spine_keepouts(plan):
        vertical = (spine[3] - spine[1]) >= (spine[2] - spine[0])
        if vertical:
            if spine[0] - x0 >= x1 - spine[2]:
                x1 = min(x1, spine[0] - 0.3)
            else:
                x0 = max(x0, spine[2] + 0.3)
        elif spine[1] - y0 >= y1 - spine[3]:
            y1 = min(y1, spine[1] - 0.3)
        else:
            y0 = max(y0, spine[3] + 0.3)
    return x0, y0, x1, y1


def _place_inner_islands(
    plan: EvChargingStationPlan,
    sample: ParkingRowSpec,
    *,
    leftover: int,
    start: int,
    wall_rows: list[ParkingRowSpec],
    aisle: float,
) -> list[ParkingRowSpec]:
    """贴墙排后的换行：与靠墙排平行，每排开口侧留回转车道。"""
    return _place_wrap_rows(
        plan, sample, leftover=leftover, start=start, wall_rows=wall_rows, aisle=aisle
    )


def _place_wrap_rows(
    plan: EvChargingStationPlan,
    sample: ParkingRowSpec,
    *,
    leftover: int,
    start: int,
    wall_rows: list[ParkingRowSpec],
    aisle: float,
) -> list[ParkingRowSpec]:
    """贴墙排后的换行：与靠墙排平行，每排开口侧留回转车道。"""
    if leftover <= 0:
        return []
    bay = float(sample.stallWidthM)
    depth = float(sample.stallLengthM)
    pitch = bay + 0.8
    if depth >= 10:
        pitch = max(pitch, bay + 0.6, 5.5)
    x0, y0, x1, y1 = _inner_courtyard(plan, wall_rows, aisle=aisle)
    inner_w = max(0.0, x1 - x0)
    inner_h = max(0.0, y1 - y0)
    if inner_w < bay - 1e-9 or inner_h < depth - 1e-9:
        return []
    wall_ids = {str(r.id or "").rsplit("_", 1)[-1] for r in wall_rows}
    along_x = bool(wall_ids & {"north", "south"}) or inner_w + 0.05 >= inner_h
    cap_line = _stalls_fit_length(inner_w if along_x else inner_h, bay=bay, pitch=pitch)
    if cap_line <= 0:
        return []
    band = depth + aisle
    from_north = along_x and "north" in wall_ids
    from_west = (not along_x) and "west" in wall_ids
    out: list[ParkingRowSpec] = []
    left = leftover
    slot = 0
    no = start
    w, h = float(plan.site.widthM), float(plan.site.heightM)
    while left > 0 and slot < 6:
        take = min(left, cap_line)
        row = sample.model_copy(deep=True)
        row.id = f"cars_wrap_{slot + 1}"
        row.stalls = take
        row.angleDeg = 0.0
        row.pitchM = pitch
        if along_x:
            row.along = "x"
            ox = x0
            oy = (y1 - depth - slot * band) if from_north else (y0 + slot * band)
            if oy < y0 - 0.05 or oy + depth > y1 + 0.05:
                break
            row.origin = PointM(x=ox, y=oy)
            ch_side = "tail" if from_north else "head"
        else:
            row.along = "y"
            oy = y0
            ox = (x0 + slot * band) if from_west else (x1 - depth - slot * band)
            if ox < x0 - 0.05 or ox + depth > x1 + 0.05:
                break
            row.origin = PointM(x=ox, y=oy)
            ch_side = "left" if from_west else "right"
        if row.charger:
            row.charger = row.charger.model_copy(update={"startNo": no, "side": ch_side})
        box = _row_aabb(row)
        if box[0] < 0.4 or box[1] < 0.4 or box[2] > w - 0.4 or box[3] > h - 0.4:
            break
        if any(_overlap(box, _row_aabb(r), pad=aisle - 0.2) for r in wall_rows + out):
            break
        if any(_overlap(box, k, pad=-0.15) for k in _gate_spine_keepouts(plan)):
            break
        out.append(row)
        no += take
        left -= take
        slot += 1
    return out


def _planned_wall_span(row: ParkingRowSpec) -> float:
    """贴墙后沿墙占用长度，与 _flush_row_to_wall 的间距一致。"""
    bay = float(row.stallWidthM)
    pitch = bay + 0.8
    if float(row.stallLengthM) >= 10:
        pitch = max(pitch, bay + 0.6, 5.5)
    return pitch * max(0, int(row.stalls) - 1) + bay


def _needed_site_wh_for_walls(
    plan: EvChargingStationPlan, walls: list[str]
) -> tuple[float, float]:
    """车位全部落在红线内所需的最小包络；不够就放大，而不是画出界。"""
    margin = 0.8
    need_w = float(plan.site.widthM)
    need_h = float(plan.site.heightM)
    if not walls or not plan.parkingRows:
        return need_w, need_h
    we_span = ns_span = 0.0
    we_depth = ns_depth = 0.0
    we_sides: set[str] = set()
    ns_sides: set[str] = set()
    truck_we = False
    truck_ns = False
    for i, row in enumerate(plan.parkingRows):
        side = walls[i % len(walls)]
        span = _planned_wall_span(row)
        depth = float(row.stallLengthM)
        truck = _is_truck_row(row)
        if side in {"west", "east"}:
            we_span = max(we_span, span)
            we_depth = max(we_depth, depth)
            we_sides.add(side)
            truck_we = truck_we or truck
        else:
            ns_span = max(ns_span, span)
            ns_depth = max(ns_depth, depth)
            ns_sides.add(side)
            truck_ns = truck_ns or truck
    if we_span:
        need_h = max(need_h, we_span + 2 * margin)
        gap = TRUCK_AISLE_M if truck_we else 0.0
        need_w = max(need_w, max(1, len(we_sides)) * we_depth + gap + 2 * margin)
    if ns_span:
        need_w = max(need_w, ns_span + 2 * margin)
        gap = TRUCK_AISLE_M if truck_ns else 0.0
        need_h = max(need_h, max(1, len(ns_sides)) * ns_depth + gap + 2 * margin)
    return math.ceil(need_w - 1e-9), math.ceil(need_h - 1e-9)


def _grow_site_envelope(plan: EvChargingStationPlan, need_w: float, need_h: float) -> bool:
    """放大用地包络并同步 polygon，避免下次 prepare 又从旧轮廓缩回去。"""
    old_w = float(plan.site.widthM)
    old_h = float(plan.site.heightM)
    new_w = max(old_w, float(need_w))
    new_h = max(old_h, float(need_h))
    if new_w <= old_w + 0.05 and new_h <= old_h + 0.05:
        return False
    poly = list(plan.site.polygon or [])
    if len(poly) >= 3 and old_w > 0.5 and old_h > 0.5:
        from api.services.layouts.brief import _shoelace_area

        pts = [(p.x, p.y) for p in poly]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        bbox = max(0.1, (max(xs) - min(xs)) * (max(ys) - min(ys)))
        area = _shoelace_area(pts) or bbox
        sx = new_w / old_w
        sy = new_h / old_h
        if area / bbox < 0.92:
            f = max(sx, sy)
            sx = sy = f
        plan.site.polygon = [PointM(x=p.x * sx, y=p.y * sy) for p in poly]
        plan.site.widthM = max(8.0, max(p.x for p in plan.site.polygon))
        plan.site.heightM = max(8.0, max(p.y for p in plan.site.polygon))
    else:
        plan.site.widthM = new_w
        plan.site.heightM = new_h
        plan.site.polygon = []
    return True


def pin_rows_against_walls(
    plan: EvChargingStationPlan,
    *,
    preferred_side: str | None = None,
    lock_envelope: bool = False,
    gentle: bool = False,
    preserve_rows: bool = False,
) -> EvChargingStationPlan:
    """靠墙布置。两排对侧放得下就对侧；否则沿四墙（+内排）装进现有红线，不改场地。"""
    out = plan.model_copy(deep=True)
    if not out.parkingRows:
        return out
    _ensure_opposing_car_rows(out)
    n_rows = len(out.parkingRows)
    if preserve_rows:
        return out
    if (
        gentle
        and not (len(out.site.polygon or []) >= 5)
        and all(_row_inside_site(r, out) for r in out.parkingRows)
    ):
        pair = set(_opposing_layout_walls(out))
        inferred = {_infer_row_wall(out, r) for r in out.parkingRows} - {None}
        angled = any(abs(float(r.angleDeg or 0)) > 5 for r in out.parkingRows)
        keep_layout = angled or inferred == pair
        adj = inferred in (
            {"north", "east"},
            {"north", "west"},
            {"south", "east"},
            {"south", "west"},
        )
        if adj and not _l_corners_blocked(out):
            keep_layout = True
        if keep_layout:
            blocked = _building_boxes(out) + _road_boxes(out)
            _separate_overlapping_rows(out)
            _nudge_all_rows(out.parkingRows, out, blocked)
            _keep_corner_clearance(out)
            _shift_rows_off_gate_throats(out, allow_grow=False)
            return out
    nonrect = len(out.site.polygon or []) >= 5
    # 锁场地时模型常已拆成四墙+场内余量；若在这里提前返回，余量会压在车道上。
    blocked_now = _building_boxes(out) + _road_boxes(out)
    if (
        n_rows >= 3
        and all(_row_inside_site(r, out) for r in out.parkingRows)
        and not lock_envelope
        and not nonrect
        and not any(_row_hits_blocked(r, out, blocked_now) for r in out.parkingRows)
        and not any(_is_truck_row(r) for r in out.parkingRows)
    ):
        blocked = _building_boxes(out) + _road_boxes(out)
        _separate_overlapping_rows(out)
        _nudge_all_rows(out.parkingRows, out, blocked)
        _keep_corner_clearance(out)
        _shift_rows_off_gate_throats(out, allow_grow=not lock_envelope)
        return out
    pair = _opposing_layout_walls(out)
    if n_rows == 1 and preferred_side in {"north", "south", "east", "west"}:
        walls = [preferred_side]
    elif n_rows == 1:
        walls = [pair[0]]
    else:
        walls = list(pair)
        if preferred_side in pair:
            other = pair[1] if preferred_side == pair[0] else pair[0]
            walls = [preferred_side, other]
    if not walls:
        return out
    if (
        _circulation_first(out)
        and not preserve_rows
        and not gentle
        and not any(_is_truck_row(r) for r in out.parkingRows)
    ):
        want = sum(int(r.stalls) for r in out.parkingRows if not _is_truck_row(r))
        cars = [r for r in out.parkingRows if not _is_truck_row(r)]
        trucks = [r for r in out.parkingRows if _is_truck_row(r)]
        if cars:
            head = cars[0].model_copy(deep=True)
            head.stalls = want
            out.parkingRows = [head, *trucks]
        if _fit_cars_into_envelope(out):
            from api.services.layouts.brief import is_electrical_room

            blocked = _road_boxes(out) + [
                _building_aabb(b)
                for b in out.buildings
                if getattr(b, "kind", "") != "demolish" and not is_electrical_room(b)
            ]
            _separate_overlapping_rows(out)
            _nudge_all_rows(out.parkingRows, out, blocked)
            _keep_corner_clearance(out)
            _shift_rows_off_gate_throats(out, allow_grow=False)
            return out
    need_w, need_h = _needed_site_wh_for_walls(out, walls)
    cur_w, cur_h = float(out.site.widthM), float(out.site.heightM)
    tight = need_w > cur_w + 0.5 or need_h > cur_h + 0.5
    leftover_inner = any(
        (not _is_truck_row(r)) and _infer_row_wall(out, r) is None
        for r in out.parkingRows
    )
    # 出图 gentle+锁场地：只轻推，禁止按第一排样本重装（否则 27–50 的 160kW 会被盖成 320kW）
    if gentle and lock_envelope:
        blocked = _building_boxes(out) + _road_boxes(out)
        _separate_overlapping_rows(out)
        _nudge_all_rows(out.parkingRows, out, blocked)
        _keep_corner_clearance(out)
        _shift_rows_off_gate_throats(out, allow_grow=False)
        return out
    if (tight or leftover_inner or (lock_envelope and n_rows >= 3)) and (
        lock_envelope or nonrect
    ):
        if _fit_cars_into_envelope(out):
            blocked = _building_boxes(out) + _road_boxes(out)
            _separate_overlapping_rows(out)
            _nudge_all_rows(out.parkingRows, out, blocked)
            _keep_corner_clearance(out)
            _shift_rows_off_gate_throats(out, allow_grow=False)
            return out
        blocked = _building_boxes(out) + _road_boxes(out)
        _nudge_all_rows(out.parkingRows, out, blocked)
        _shift_rows_off_gate_throats(out, allow_grow=False)
        return out
    if not lock_envelope and not nonrect:
        _grow_site_envelope(out, need_w, need_h)
    blocked = _building_boxes(out) + _road_boxes(out)
    for i, row in enumerate(out.parkingRows):
        side = walls[i % len(walls)]
        _flush_row_to_wall(row, out, side)
        slot = i // max(1, len(walls))
        if slot:
            gap = float(row.stallLengthM) + (
                TRUCK_AISLE_M if _is_truck_row(row) else CAR_AISLE_M
            )
            if side == "west":
                _shift_row(row, gap * slot, 0.0)
            elif side == "east":
                _shift_row(row, -gap * slot, 0.0)
            elif side == "north":
                _shift_row(row, 0.0, -gap * slot)
            else:
                _shift_row(row, 0.0, gap * slot)
    _separate_overlapping_rows(out)
    _nudge_all_rows(out.parkingRows, out, blocked)
    _separate_overlapping_rows(out)
    _keep_corner_clearance(out)
    _shift_rows_off_gate_throats(out, allow_grow=not lock_envelope)
    return out


def _separate_overlapping_rows(plan: EvChargingStationPlan) -> None:
    """车位排互不重叠：同向沿开口让；转角两排把后排挪出角落。"""
    rows = plan.parkingRows
    for _ in range(10):
        moved = False
        for i in range(len(rows)):
            for j in range(i + 1, len(rows)):
                if not _overlap(_row_aabb(rows[i]), _row_aabb(rows[j]), pad=0.15):
                    continue
                wi = _infer_row_wall(plan, rows[i])
                wj = _infer_row_wall(plan, rows[j])
                pair = {wi, wj}
                if pair == {"north", "east"}:
                    east = rows[i] if wi == "east" else rows[j]
                    north = rows[j] if wi == "east" else rows[i]
                    nb, eb = _row_aabb(north), _row_aabb(east)
                    if eb[3] > nb[1] - 0.2:
                        _shift_row(east, 0.0, nb[1] - 0.45 - eb[3])
                    if nb[2] > eb[0] - 0.2:
                        _shift_row(north, eb[0] - 0.45 - nb[2], 0.0)
                    moved = True
                    continue
                if pair == {"north", "west"}:
                    west = rows[i] if wi == "west" else rows[j]
                    north = rows[j] if wi == "west" else rows[i]
                    nb, wb = _row_aabb(north), _row_aabb(west)
                    if wb[3] > nb[1] - 0.2:
                        _shift_row(west, 0.0, nb[1] - 0.45 - wb[3])
                    if nb[0] < wb[2] + 0.2:
                        _shift_row(north, wb[2] + 0.45 - nb[0], 0.0)
                    moved = True
                    continue
                if pair == {"south", "east"}:
                    east = rows[i] if wi == "east" else rows[j]
                    south = rows[j] if wi == "east" else rows[i]
                    sb, eb = _row_aabb(south), _row_aabb(east)
                    if eb[1] < sb[3] + 0.2:
                        _shift_row(east, 0.0, sb[3] + 0.45 - eb[1])
                    if sb[2] > eb[0] - 0.2:
                        _shift_row(south, eb[0] - 0.45 - sb[2], 0.0)
                    moved = True
                    continue
                if pair == {"south", "west"}:
                    west = rows[i] if wi == "west" else rows[j]
                    south = rows[j] if wi == "west" else rows[i]
                    sb, wb = _row_aabb(south), _row_aabb(west)
                    if wb[1] < sb[3] + 0.2:
                        _shift_row(west, 0.0, sb[3] + 0.45 - wb[1])
                    if sb[0] < wb[2] + 0.2:
                        _shift_row(south, wb[2] + 0.45 - sb[0], 0.0)
                    moved = True
                    continue
                wall = wj or wi or "west"
                step = float(rows[j].stallWidthM)
                if wall == "west":
                    _shift_row(rows[j], 0.0, step)
                elif wall == "east":
                    _shift_row(rows[j], 0.0, step)
                elif wall == "north":
                    _shift_row(rows[j], step, 0.0)
                else:
                    _shift_row(rows[j], step, 0.0)
                moved = True
        if not moved:
            break


def _keep_corner_clearance(plan: EvChargingStationPlan) -> None:
    """万一出现转角两排：开口侧至少留一个车长，避免端头车位倒不出来。"""
    by_wall: dict[str, list[ParkingRowSpec]] = {}
    for row in plan.parkingRows:
        wall = _infer_row_wall(plan, row)
        if wall:
            by_wall.setdefault(wall, []).append(row)

    def _inset_north_from_west(north: ParkingRowSpec, west: ParkingRowSpec) -> None:
        nb, wb = _row_aabb(north), _row_aabb(west)
        need = max(float(west.stallLengthM), CAR_AISLE_M) + 0.6
        if nb[0] < wb[2] + need:
            _shift_row(north, wb[2] + need - nb[0], 0.0)

    def _inset_west_from_north(west: ParkingRowSpec, north: ParkingRowSpec) -> None:
        nb, wb = _row_aabb(north), _row_aabb(west)
        need = CAR_AISLE_M
        if wb[3] > nb[1] - need:
            _shift_row(west, 0.0, (nb[1] - need) - wb[3])

    if by_wall.get("north") and by_wall.get("west"):
        _inset_north_from_west(by_wall["north"][0], by_wall["west"][0])
        _inset_west_from_north(by_wall["west"][0], by_wall["north"][0])
    if by_wall.get("north") and by_wall.get("east"):
        north, east = by_wall["north"][0], by_wall["east"][0]
        nb, eb = _row_aabb(north), _row_aabb(east)
        need = max(float(east.stallLengthM), CAR_AISLE_M) + 0.6
        if nb[2] > eb[0] - need:
            _shift_row(north, (eb[0] - need) - nb[2], 0.0)
        if eb[3] > nb[1] - CAR_AISLE_M:
            _shift_row(east, 0.0, (nb[1] - CAR_AISLE_M) - eb[3])
    if by_wall.get("south") and by_wall.get("west"):
        south, west = by_wall["south"][0], by_wall["west"][0]
        sb, wb = _row_aabb(south), _row_aabb(west)
        need = max(float(west.stallLengthM), CAR_AISLE_M) + 0.6
        if sb[0] < wb[2] + need:
            _shift_row(south, wb[2] + need - sb[0], 0.0)
        if wb[1] < sb[3] + CAR_AISLE_M:
            _shift_row(west, 0.0, sb[3] + CAR_AISLE_M - wb[1])
    if by_wall.get("south") and by_wall.get("east"):
        south, east = by_wall["south"][0], by_wall["east"][0]
        sb, eb = _row_aabb(south), _row_aabb(east)
        need = max(float(east.stallLengthM), CAR_AISLE_M) + 0.6
        if sb[2] > eb[0] - need:
            _shift_row(south, (eb[0] - need) - sb[2], 0.0)
        if eb[1] < sb[3] + CAR_AISLE_M:
            _shift_row(east, 0.0, sb[3] + CAR_AISLE_M - eb[1])


def _gate_throat_keepouts(plan: EvChargingStationPlan) -> list[Aabb]:
    """大门开口内侧禁停车：改车位让路，不改门位。"""
    w, h = float(plan.site.widthM), float(plan.site.heightM)
    depth = min(CAR_AISLE_M, max(4.0, 0.2 * min(w, h)))
    if any(_is_truck_row(r) for r in plan.parkingRows):
        depth = min(TRUCK_AISLE_M, max(depth, 8.0))
    pad = 0.5
    out: list[Aabb] = []
    for gate in collect_site_gates(plan.site):
        o, wd = float(gate.offsetM), float(gate.widthM)
        if gate.side == "south":
            out.append((o - pad, 0.0, o + wd + pad, depth))
        elif gate.side == "north":
            out.append((o - pad, h - depth, o + wd + pad, h))
        elif gate.side == "west":
            out.append((0.0, o - pad, depth, o + wd + pad))
        else:
            out.append((w - depth, o - pad, w, o + wd + pad))
    return out


def _shift_rows_off_gate_throats(
    plan: EvChargingStationPlan, *, allow_grow: bool = True
) -> None:
    keepouts = _gate_throat_keepouts(plan) + _gate_spine_keepouts(plan)
    if not keepouts:
        return
    w, h = float(plan.site.widthM), float(plan.site.heightM)
    for row in plan.parkingRows:
        wall = _infer_row_wall(plan, row)
        if not wall:
            continue
        for _ in range(36):
            box = _row_aabb(row)
            hit = next((k for k in keepouts if _overlap(box, k, pad=-0.05)), None)
            if hit is None:
                break
            if wall in {"west", "east"}:
                if hit[1] <= 0.25:
                    _shift_row(row, 0.0, max(0.2, hit[3] + 0.3 - box[1]))
                else:
                    _shift_row(row, 0.0, -max(0.2, box[3] - (hit[1] - 0.3)))
            elif hit[0] <= 0.25:
                _shift_row(row, max(0.2, hit[2] + 0.3 - box[0]), 0.0)
            else:
                _shift_row(row, -max(0.2, box[2] - (hit[0] - 0.3)), 0.0)
        box = _row_aabb(row)
        hit = next((k for k in keepouts if _overlap(box, k, pad=-0.05)), None)
        if hit is not None:
            lo, hi = _along_available(plan, wall)
            _crop_row_to_along(row, wall, lo=lo, hi=hi)
            box = _row_aabb(row)
        dx = dy = 0.0
        if box[0] < 0.4:
            dx += 0.4 - box[0]
        if box[1] < 0.4:
            dy += 0.4 - box[1]
        if abs(dx) > 0.02 or abs(dy) > 0.02:
            _shift_row(row, dx, dy)
            box = _row_aabb(row)
        if site_is_irregular(plan.site) or not allow_grow:
            clamp_x = 0.0
            clamp_y = 0.0
            if box[2] > w - 0.4:
                clamp_x -= box[2] - (w - 0.4)
            if box[3] > h - 0.4:
                clamp_y -= box[3] - (h - 0.4)
            if abs(clamp_x) > 0.02 or abs(clamp_y) > 0.02:
                _shift_row(row, clamp_x, clamp_y)
            continue
        need_w = max(w, box[2] + 0.8)
        need_h = max(h, box[3] + 0.8)
        if need_w > w + 0.05 or need_h > h + 0.05:
            _grow_site_envelope(plan, need_w, need_h)
            w, h = float(plan.site.widthM), float(plan.site.heightM)


def _apply_wall_mount_chargers(plan: EvChargingStationPlan) -> None:
    """靠墙车位：充电桩必须贴墙侧，开口朝向场内供车辆进出。"""
    for row in plan.parkingRows:
        if not row.charger or row.charger.type == "none":
            continue
        wall = _infer_row_wall(plan, row)
        if not wall:
            continue
        side = charger_side_for_wall(wall)
        row.charger = row.charger.model_copy(update={"side": side})


def _eq_half() -> float:
    return 1.55


def _eq_box(x: float, y: float) -> Aabb:
    h = _eq_half()
    return (x - h, y - h, x + h, y + h)


def _is_site_central(x: float, y: float, w: float, h: float, *, frac: float = 0.30) -> bool:
    return frac * w < x < (1.0 - frac) * w and frac * h < y < (1.0 - frac) * h


def _feeder_point_ok(
    x: float,
    y: float,
    *,
    w: float,
    h: float,
    buildings: list[Aabb],
    row_boxes: list[Aabb],
    keepouts: list[Aabb] | None = None,
) -> bool:
    half = _eq_half()
    if not (half + 0.4 <= x <= w - half - 0.4 and half + 0.4 <= y <= h - half - 0.4):
        return False
    box = _eq_box(x, y)
    if any(_overlap(box, b, pad=-0.15) for b in buildings):
        return False
    if any(_overlap(box, rb, pad=-0.2) for rb in row_boxes):
        return False
    if keepouts and any(_overlap(box, k, pad=-0.1) for k in keepouts):
        return False
    return True


def _aisle_keepouts(plan: EvChargingStationPlan) -> list[Aabb]:
    """行车过道禁放区：箱变、树木、绿化都不得压进去。"""
    out: list[Aabb] = []
    for aisle in plan.aisles:
        box = _aisle_aabb(aisle)
        if box is not None:
            out.append(box)
    return out


def _row_opening_keepouts(plan: EvChargingStationPlan) -> list[Aabb]:
    """车位开口侧进出带：即使车道 AABB 没覆盖到，也不许再往这里堆设备。"""
    out: list[Aabb] = []
    for row in plan.parkingRows:
        box = _row_aabb(row)
        depth = TRUCK_AISLE_M if _is_truck_row(row) else CAR_AISLE_M
        wall = _infer_row_wall(plan, row)
        side = wall
        if not side and row.charger:
            ch = row.charger.side
            if row.along == "x":
                side = "north" if ch == "head" else "south" if ch == "tail" else None
            else:
                side = "east" if ch == "right" else "west" if ch == "left" else None
        if side == "north":
            out.append((box[0], box[1] - depth, box[2], box[1]))
        elif side == "south":
            out.append((box[0], box[3], box[2], box[3] + depth))
        elif side == "west":
            out.append((box[2], box[1], box[2] + depth, box[3]))
        elif side == "east":
            out.append((box[0] - depth, box[1], box[0], box[3]))
    return out


def _between_row_drive_keepouts(plan: EvChargingStationPlan) -> list[Aabb]:
    """相邻两排之间的空档一律当行车道，禁止放设备（换行后视觉马路常比 7m 过道 AABB 更宽）。"""
    boxes = [_row_aabb(r) for r in plan.parkingRows]
    out: list[Aabb] = []
    for i, a in enumerate(boxes):
        for b in boxes[i + 1 :]:
            x_ov = min(a[2], b[2]) - max(a[0], b[0])
            if x_ov >= 4.0:
                lo, hi = (a, b) if a[3] <= b[1] else (b, a)
                gap = hi[1] - lo[3]
                if 1.5 <= gap <= 28.0:
                    out.append((max(a[0], b[0]), lo[3], min(a[2], b[2]), hi[1]))
            y_ov = min(a[3], b[3]) - max(a[1], b[1])
            if y_ov >= 4.0:
                lo, hi = (a, b) if a[2] <= b[0] else (b, a)
                gap = hi[0] - lo[2]
                if 1.5 <= gap <= 28.0:
                    out.append((lo[2], max(a[1], b[1]), hi[0], min(a[3], b[3])))
    return out


def _gate_throat_keepouts(plan: EvChargingStationPlan) -> list[Aabb]:
    """只禁门洞前方，不把整条出入口边都封死（角落空白地仍可放配电室）。"""
    w, h = float(plan.site.widthM), float(plan.site.heightM)
    depth = 10.0
    pad = 2.0
    out: list[Aabb] = []
    for gate in collect_site_gates(plan.site):
        lo = float(gate.offsetM) - pad
        hi = float(gate.offsetM) + float(gate.widthM) + pad
        if gate.side == "south":
            out.append((lo, 0.0, hi, depth))
        elif gate.side == "north":
            out.append((lo, h - depth, hi, h))
        elif gate.side == "west":
            out.append((0.0, lo, depth, hi))
        else:
            out.append((w - depth, lo, w, hi))
    return out


def _equipment_keepouts(plan: EvChargingStationPlan) -> list[Aabb]:
    return _gate_throat_keepouts(plan) + _aisle_keepouts(plan) + _gate_spine_keepouts(plan)


def _perimeter_feeder_candidates(w: float, h: float, *, step: float = 3.2) -> list[tuple[float, float]]:
    """沿围墙内侧扫点：过道和大门禁区之外的靠墙空位。"""
    m = 2.4
    pts: list[tuple[float, float]] = []
    x = m
    while x <= w - m + 1e-6:
        pts.append((x, m))
        pts.append((x, h - m))
        x += step
    y = m
    while y <= h - m + 1e-6:
        pts.append((m, y))
        pts.append((w - m, y))
        y += step
    uniq: list[tuple[float, float]] = []
    for p in pts:
        if not any(abs(p[0] - q[0]) < 0.4 and abs(p[1] - q[1]) < 0.4 for q in uniq):
            uniq.append(p)
    return uniq


def _equipment_gate_keepouts(plan: EvChargingStationPlan) -> list[Aabb]:
    """出入口前方整条边禁放箱变，避免贴在大门两侧。"""
    w, h = float(plan.site.widthM), float(plan.site.heightM)
    depth = 8.0
    out: list[Aabb] = []
    for gate in collect_site_gates(plan.site):
        if gate.side == "south":
            out.append((0.0, 0.0, w, depth))
        elif gate.side == "north":
            out.append((0.0, h - depth, w, h))
        elif gate.side == "west":
            out.append((0.0, 0.0, depth, h))
        else:
            out.append((w - depth, 0.0, w, h))
    return out


def _corner_feeder_candidates(
    *, wall: str, w: float, h: float
) -> list[tuple[float, float]]:
    """箱变优先放在该排所靠墙的远门角落。"""
    nw, ne = (2.4, h - 2.4), (w - 2.4, h - 2.4)
    sw, se = (2.4, 2.4), (w - 2.4, 2.4)
    by_wall = {
        "west": [nw, sw],
        "east": [ne, se],
        "north": [nw, ne],
        "south": [sw, se],
    }
    prefer = by_wall.get(wall) or [nw, ne, sw, se]
    rest = [p for p in (nw, ne, sw, se) if p not in prefer]
    return prefer + rest


def _feeder_open_side_candidates(
    *,
    wall: str,
    box: Aabb,
    cx: float,
    cy: float,
    w: float,
    h: float,
    slot: int = 0,
) -> list[tuple[float, float]]:
    """箱变落在对应充电排沿墙端头，不进场中行车道。"""
    extra = slot * 3.2
    end_g = (2.6 + extra, 3.6 + extra, 4.8 + extra)
    cands: list[tuple[float, float]] = []

    def _add(x: float, y: float) -> None:
        cands.append((x, y))

    if wall == "north":
        yw = min(h - 2.2, max(box[3] - 1.6, box[1] + 1.0))
        for g in end_g:
            _add(box[0] - g, yw)
            _add(box[2] + g, yw)
        _add(box[0] - 2.8, min(h - 2.2, box[3] - 0.8))
        _add(box[2] + 2.8, min(h - 2.2, box[3] - 0.8))
    elif wall == "south":
        yw = max(2.2, min(box[1] + 1.6, box[3] - 1.0))
        for g in end_g:
            _add(box[0] - g, yw)
            _add(box[2] + g, yw)
        _add(box[0] - 2.8, max(2.2, box[1] + 0.8))
        _add(box[2] + 2.8, max(2.2, box[1] + 0.8))
    elif wall == "east":
        xw = min(w - 2.2, max(box[2] - 1.6, box[0] + 1.0))
        for g in end_g:
            _add(xw, box[1] - g)
            _add(xw, box[3] + g)
        _add(min(w - 2.2, box[2] - 0.8), box[1] - 2.8)
        _add(min(w - 2.2, box[2] - 0.8), box[3] + 2.8)
    elif wall == "west":
        xw = max(2.2, min(box[0] + 1.6, box[2] - 1.0))
        for g in end_g:
            _add(xw, box[1] - g)
            _add(xw, box[3] + g)
        _add(max(2.2, box[0] + 0.8), box[1] - 2.8)
        _add(max(2.2, box[0] + 0.8), box[3] + 2.8)
    else:
        dist = {
            "west": cx,
            "east": w - cx,
            "south": cy,
            "north": h - cy,
        }
        near = min(dist, key=dist.get)  # type: ignore[arg-type]
        return _feeder_open_side_candidates(
            wall=near, box=box, cx=cx, cy=cy, w=w, h=h, slot=slot
        )

    uniq: list[tuple[float, float]] = []
    for p in cands:
        if not any(abs(p[0] - q[0]) < 0.25 and abs(p[1] - q[1]) < 0.25 for q in uniq):
            uniq.append(p)
    return uniq


def _search_free_feeder_point(
    *,
    seed: tuple[float, float],
    w: float,
    h: float,
    buildings: list[Aabb],
    row_boxes: list[Aabb],
    occupied: list[tuple[float, float]],
    keepouts: list[Aabb] | None = None,
) -> tuple[float, float] | None:
    """以种子点为中心扩圈找不压车位、不挡出入口、不压过道的落点。"""
    sx, sy = seed
    best: tuple[float, float] | None = None
    best_score = 1e18
    for rad in (0.0, 1.5, 3.0, 4.5, 6.0, 8.0, 10.0, 12.0, 16.0):
        ring = [(sx, sy)] if rad < 0.1 else []
        for k in range(16):
            ang = (2 * math.pi * k) / 16
            ring.append((sx + rad * math.cos(ang), sy + rad * math.sin(ang)))
        for x, y in ring:
            if not _feeder_point_ok(
                x, y, w=w, h=h, buildings=buildings, row_boxes=row_boxes, keepouts=keepouts
            ):
                continue
            if any(abs(x - ox) < 2.8 and abs(y - oy) < 2.8 for ox, oy in occupied):
                continue
            edge = min(x, w - x, y, h - y)
            central_pen = 40.0 if _is_site_central(x, y, w, h) else 0.0
            score = (x - sx) ** 2 + (y - sy) ** 2 + central_pen - edge * 2.0
            if score < best_score:
                best_score = score
                best = (x, y)
        if best is not None and rad >= 3.0:
            break
    if best is None:
        for x, y in _perimeter_feeder_candidates(w, h):
            if not _feeder_point_ok(
                x, y, w=w, h=h, buildings=buildings, row_boxes=row_boxes, keepouts=keepouts
            ):
                continue
            if any(abs(x - ox) < 2.8 and abs(y - oy) < 2.8 for ox, oy in occupied):
                continue
            dist = (x - sx) ** 2 + (y - sy) ** 2
            if dist < best_score:
                best_score = dist
                best = (x, y)
    if best is None:
        best = _grid_scan_feeder_point(
            w=w, h=h, buildings=buildings, row_boxes=row_boxes, occupied=occupied, keepouts=keepouts
        )
    return best


def _grid_scan_feeder_point(
    *,
    w: float,
    h: float,
    buildings: list[Aabb],
    row_boxes: list[Aabb],
    occupied: list[tuple[float, float]],
    keepouts: list[Aabb] | None = None,
    step: float = 1.6,
) -> tuple[float, float] | None:
    """整场扫空白点：道路/开口/车位之外、靠边缘优先。"""
    best: tuple[float, float] | None = None
    best_score = 1e18
    y = 2.4
    while y <= h - 2.4 + 1e-6:
        x = 2.4
        while x <= w - 2.4 + 1e-6:
            if _feeder_point_ok(
                x, y, w=w, h=h, buildings=buildings, row_boxes=row_boxes, keepouts=keepouts
            ) and not any(abs(x - ox) < 2.8 and abs(y - oy) < 2.8 for ox, oy in occupied):
                edge = min(x, w - x, y, h - y)
                central_pen = 50.0 if _is_site_central(x, y, w, h) else 0.0
                score = central_pen - edge
                if score < best_score:
                    best_score = score
                    best = (x, y)
            x += step
        y += step
    return best


def _building_aabb(b: BuildingSpec) -> Aabb:
    r = b.rect
    return (float(r.x), float(r.y), float(r.x) + float(r.w), float(r.y) + float(r.h))


def _electrical_rooms(plan: EvChargingStationPlan) -> list[BuildingSpec]:
    from api.services.layouts.brief import is_electrical_room

    return [b for b in plan.buildings if is_electrical_room(b)]


def _rect_in_site(rect: Aabb, w: float, h: float, *, margin: float = 0.6) -> bool:
    return (
        rect[0] >= margin - 1e-6
        and rect[1] >= margin - 1e-6
        and rect[2] <= w - margin + 1e-6
        and rect[3] <= h - margin + 1e-6
        and rect[2] - rect[0] >= 3.0
        and rect[3] - rect[1] >= 3.0
    )


def _blank_occupied(
    plan: EvChargingStationPlan,
    *,
    skip_rooms: bool = False,
    skip_feeders: bool = False,
    skip_eq_ids: set[str] | None = None,
) -> list[Aabb]:
    """已占用：车位、过道、开口带、门洞、建筑、已放设备。新东西只能落在剩下的空白地。"""
    from api.services.layouts.brief import is_electrical_room

    boxes: list[Aabb] = [_row_aabb(r) for r in plan.parkingRows]
    boxes.extend(_aisle_keepouts(plan))
    boxes.extend(_row_opening_keepouts(plan))
    boxes.extend(_between_row_drive_keepouts(plan))
    boxes.extend(_gate_throat_keepouts(plan))
    boxes.extend(_gate_spine_keepouts(plan))
    boxes.extend(_road_boxes(plan))
    for b in plan.buildings:
        if b.kind == "demolish":
            continue
        if skip_rooms and is_electrical_room(b):
            continue
        boxes.append(_building_aabb(b))
    for eq in plan.equipment:
        if skip_eq_ids and eq.id in skip_eq_ids:
            continue
        if skip_feeders and eq.type in _FEEDER_TYPES:
            continue
        boxes.append(_eq_box(float(eq.x), float(eq.y)))
    return boxes


def _blank_rect_ok(rect: Aabb, occupied: list[Aabb], *, w: float, h: float) -> bool:
    if not _rect_in_site(rect, w, h):
        return False
    return not any(_overlap(rect, o, pad=-0.12) for o in occupied)


def _gate_is_southeast(plan: EvChargingStationPlan) -> bool:
    w = float(plan.site.widthM)
    for gate in collect_site_gates(plan.site):
        if gate.side != "south":
            continue
        mid = float(gate.offsetM) + float(gate.widthM) / 2.0
        if mid >= w * 0.55:
            return True
    return False


def _gate_is_northeast(plan: EvChargingStationPlan) -> bool:
    w = float(plan.site.widthM)
    for gate in collect_site_gates(plan.site):
        if gate.side != "north":
            continue
        mid = float(gate.offsetM) + float(gate.widthM) / 2.0
        if mid >= w * 0.55:
            return True
    return False


def _room_size_options(tx_n: int) -> list[tuple[float, float]]:
    n = max(1, tx_n)
    long = max(8.0, 3.4 * n + 3.2)
    short = 6.2
    return [(long, short), (short, long), (long * 0.85, short), (short * 0.9, long * 0.9)]


def _room_rect_candidates(
    w: float, h: float, rw: float, rh: float, *, skip_se: bool, skip_ne: bool = False
) -> list[Aabb]:
    m = 0.8
    cands: list[Aabb] = [
        (m, h - m - rh, m + rw, h - m),
        (m, m, m + rw, m + rh),
    ]
    if not skip_ne:
        cands.append((w - m - rw, h - m - rh, w - m, h - m))
    if not skip_se:
        cands.append((w - m - rw, m, w - m, m + rh))
    y = m
    while y + rh <= h - m + 1e-6:
        cands.append((m, y, m + rw, y + rh))
        cands.append((w - m - rw, y, w - m, y + rh))
        y += 1.0
    x = m
    while x + rw <= w - m + 1e-6:
        cands.append((x, h - m - rh, x + rw, h - m))
        cands.append((x, m, x + rw, m + rh))
        x += 1.0
    return cands


def _place_electrical_room(plan: EvChargingStationPlan) -> None:
    rooms = _electrical_rooms(plan)
    if not rooms:
        return
    room = rooms[0]
    w, h = float(plan.site.widthM), float(plan.site.heightM)
    occupied = _blank_occupied(plan, skip_rooms=True, skip_feeders=True)
    cur = _building_aabb(room)
    if _blank_rect_ok(cur, occupied, w=w, h=h):
        return
    skip_se = _gate_is_southeast(plan)
    skip_ne = _gate_is_northeast(plan)
    n_tx = max(1, sum(1 for eq in plan.equipment if eq.type in _FEEDER_TYPES))
    for rw, rh in _room_size_options(n_tx):
        for rect in _room_rect_candidates(w, h, rw, rh, skip_se=skip_se, skip_ne=skip_ne):
            if not _blank_rect_ok(rect, occupied, w=w, h=h):
                continue
            room.rect = RectM(
                x=rect[0],
                y=rect[1],
                w=rect[2] - rect[0],
                h=rect[3] - rect[1],
            )
            room.label = room.label or "配电室"
            return


def _seat_transformers_in_room(plan: EvChargingStationPlan) -> bool:
    rooms = _electrical_rooms(plan)
    if not rooms:
        return False
    r = rooms[0].rect
    feeders = [eq for eq in plan.equipment if eq.type in _FEEDER_TYPES]
    if not feeders:
        return True
    n = len(feeders)
    pad = 1.8
    if float(r.w) >= float(r.h):
        span = max(0.8, float(r.w) - 2 * pad)
        for i, eq in enumerate(feeders):
            eq.x = float(r.x) + pad + (i + 0.5) * span / n
            eq.y = float(r.y) + float(r.h) * 0.5
    else:
        span = max(0.8, float(r.h) - 2 * pad)
        for i, eq in enumerate(feeders):
            eq.x = float(r.x) + float(r.w) * 0.5
            eq.y = float(r.y) + pad + (i + 0.5) * span / n
    return True


def _host_seed_points(plan: EvChargingStationPlan) -> list[tuple[float, float]]:
    w, h = float(plan.site.widthM), float(plan.site.heightM)
    seeds: list[tuple[float, float]] = []
    for row in plan.parkingRows:
        box = _row_aabb(row)
        wall = _infer_row_wall(plan, row)
        if wall == "north":
            yw = min(h - 2.2, max(box[1] + 1.2, box[3] - 1.2))
            seeds.extend(((box[0] - 2.6, yw), (box[2] + 2.6, yw)))
        elif wall == "south":
            yw = max(2.2, min(box[1] + 1.6, box[3] - 1.0))
            seeds.extend(((box[0] - 2.6, yw), (box[2] + 2.6, yw)))
        elif wall == "east":
            xw = min(w - 2.2, max(box[2] - 1.6, box[0] + 1.0))
            seeds.extend(((xw, box[1] - 2.6), (xw, box[3] + 2.6)))
        elif wall == "west":
            xw = max(2.2, min(box[0] + 1.6, box[2] - 1.0))
            seeds.extend(((xw, box[1] - 2.6), (xw, box[3] + 2.6)))
        elif row.along == "x":
            yw = (box[1] + box[3]) * 0.5
            seeds.extend(((box[0] - 2.6, yw), (box[2] + 2.6, yw)))
        else:
            xw = (box[0] + box[2]) * 0.5
            seeds.extend(((xw, box[1] - 2.6), (xw, box[3] + 2.6)))
    seeds.extend(_perimeter_feeder_candidates(w, h))
    uniq: list[tuple[float, float]] = []
    for p in seeds:
        if not any(abs(p[0] - q[0]) < 0.4 and abs(p[1] - q[1]) < 0.4 for q in uniq):
            uniq.append(p)
    return uniq


def _place_group_hosts_on_blank(plan: EvChargingStationPlan) -> None:
    hosts = [
        eq
        for eq in plan.equipment
        if eq.type == "group_host" or any(tok in (eq.label or "") for tok in ("群冲", "群充"))
    ]
    if not hosts:
        return
    w, h = float(plan.site.widthM), float(plan.site.heightM)
    buildings = _building_boxes(plan)
    row_boxes = [_row_aabb(r) for r in plan.parkingRows]
    keepouts = (
        _equipment_keepouts(plan)
        + _row_opening_keepouts(plan)
        + _between_row_drive_keepouts(plan)
    )
    heads: list[tuple[float, float]] = []
    for row in plan.parkingRows:
        heads.extend(charger_heads(row))
    occupied: list[tuple[float, float]] = [
        (float(eq.x), float(eq.y)) for eq in plan.equipment if eq not in hosts
    ]
    seeds = _host_seed_points(plan)
    for eq in hosts:
        skip = {eq.id}
        occ_boxes = _blank_occupied(plan, skip_eq_ids=skip)
        chosen: tuple[float, float] | None = None

        def _rank(p: tuple[float, float]) -> tuple:
            x, y = p
            ok = _feeder_point_ok(
                x, y, w=w, h=h, buildings=buildings, row_boxes=row_boxes, keepouts=keepouts
            ) and not any(_overlap(_eq_box(x, y), o, pad=-0.12) for o in occ_boxes)
            pile_d = (
                min(math.hypot(x - hx, y - hy) for hx, hy in heads) if heads else 0.0
            )
            corner_d = min(
                math.hypot(x - cx, y - cy)
                for cx, cy in (
                    (2.4, 2.4),
                    (w - 2.4, 2.4),
                    (2.4, h - 2.4),
                    (w - 2.4, h - 2.4),
                )
            )
            return (
                0 if ok else 1,
                1 if _is_site_central(x, y, w, h) else 0,
                pile_d,
                0 if corner_d < 10.0 else 1,
            )

        for p in sorted(seeds, key=_rank):
            x, y = p
            if not _feeder_point_ok(
                x, y, w=w, h=h, buildings=buildings, row_boxes=row_boxes, keepouts=keepouts
            ):
                continue
            if any(_overlap(_eq_box(x, y), o, pad=-0.12) for o in occ_boxes):
                continue
            if any(abs(x - ox) < 2.8 and abs(y - oy) < 2.8 for ox, oy in occupied):
                continue
            chosen = p
            break
        if chosen is None:
            seed = seeds[0] if seeds else (4.0, 4.0)
            found = _search_free_feeder_point(
                seed=seed,
                w=w,
                h=h,
                buildings=buildings,
                row_boxes=row_boxes,
                occupied=occupied,
                keepouts=keepouts,
            )
            chosen = found
        if chosen is None:
            continue
        eq.x, eq.y = float(chosen[0]), float(chosen[1])
        eq.type = "group_host"
        occupied.append((eq.x, eq.y))


def _apply_blank_ground_equipment(plan: EvChargingStationPlan) -> None:
    """有配电室则箱变进室内；群冲贴桩端头空白地。一律不压车位/过道。"""
    _place_electrical_room(plan)
    if _electrical_rooms(plan) and _seat_transformers_in_room(plan):
        pass
    else:
        _place_equipment_near_chargers(plan)
    _place_group_hosts_on_blank(plan)


def _place_equipment_near_chargers(plan: EvChargingStationPlan) -> None:
    """箱变放在远离出入口的角落、沿墙靠近对应充电排；禁止挡门、压车位、压过道、堆场中。"""
    feeders = [eq for eq in plan.equipment if eq.type in _FEEDER_TYPES]
    others = [eq for eq in plan.equipment if eq.type not in _FEEDER_TYPES]
    if not feeders:
        return
    rows = [r for r in plan.parkingRows if charger_heads(r)]
    if not rows:
        return
    w, h = float(plan.site.widthM), float(plan.site.heightM)
    buildings = _building_boxes(plan)
    row_boxes = [_row_aabb(r) for r in plan.parkingRows]
    keepouts = (
        _equipment_keepouts(plan)
        + _row_opening_keepouts(plan)
        + _between_row_drive_keepouts(plan)
    )
    placed: list = []
    occupied: list[tuple[float, float]] = []

    for i, eq in enumerate(feeders):
        row = rows[i % len(rows)]
        heads = charger_heads(row)
        box = _row_aabb(row)
        cx = sum(p[0] for p in heads) / len(heads)
        cy = sum(p[1] for p in heads) / len(heads)
        wall = _infer_row_wall(plan, row) or ""
        slot = i // max(1, len(rows))
        cands = [
            *_corner_feeder_candidates(wall=wall, w=w, h=h),
            *_feeder_open_side_candidates(
                wall=wall, box=box, cx=cx, cy=cy, w=w, h=h, slot=slot
            ),
            *_perimeter_feeder_candidates(w, h),
        ]

        def _rank(p: tuple[float, float]) -> tuple:
            x, y = p
            ok = _feeder_point_ok(
                x, y, w=w, h=h, buildings=buildings, row_boxes=row_boxes, keepouts=keepouts
            )
            sep = (
                min(abs(x - ox) + abs(y - oy) for ox, oy in occupied) if occupied else 99.0
            )
            head_d = min(math.hypot(x - hx, y - hy) for hx, hy in heads)
            wall_d = min(x, w - x, y, h - y)
            return (
                0 if ok else 1,
                1 if _is_site_central(x, y, w, h) else 0,
                0 if wall_d < 6.0 else 1,
                head_d,
                0 if sep >= 2.8 else 1,
            )

        ranked = sorted(cands, key=_rank)
        chosen: tuple[float, float] | None = None
        for p in ranked:
            x, y = p
            if not _feeder_point_ok(
                x, y, w=w, h=h, buildings=buildings, row_boxes=row_boxes, keepouts=keepouts
            ):
                continue
            if any(abs(x - ox) < 2.8 and abs(y - oy) < 2.8 for ox, oy in occupied):
                continue
            chosen = p
            break
        if chosen is None:
            seed = (2.4, h * 0.45)
            chosen = _search_free_feeder_point(
                seed=seed,
                w=w,
                h=h,
                buildings=buildings,
                row_boxes=row_boxes,
                occupied=occupied,
                keepouts=keepouts,
            )
        if chosen is None:
            # 最后兜底：场地四角中距本排最近且空闲者
            corners = [
                (2.4, 2.4),
                (w - 2.4, 2.4),
                (2.4, h - 2.4),
                (w - 2.4, h - 2.4),
            ]
            corners = sorted(corners, key=lambda p: abs(p[0] - cx) + abs(p[1] - cy))
            for p in corners:
                if _feeder_point_ok(
                    p[0],
                    p[1],
                    w=w,
                    h=h,
                    buildings=buildings,
                    row_boxes=row_boxes,
                    keepouts=keepouts,
                ):
                    chosen = p
                    break
        if chosen is None:
            for p in _perimeter_feeder_candidates(w, h):
                if _feeder_point_ok(
                    p[0],
                    p[1],
                    w=w,
                    h=h,
                    buildings=buildings,
                    row_boxes=row_boxes,
                    keepouts=keepouts,
                ) and not any(abs(p[0] - ox) < 2.8 and abs(p[1] - oy) < 2.8 for ox, oy in occupied):
                    chosen = p
                    break
        if chosen is None:
            fallback = (min(w - 2.4, max(2.4, float(eq.x))), min(h - 2.4, max(2.4, float(eq.y))))
            if _feeder_point_ok(
                fallback[0],
                fallback[1],
                w=w,
                h=h,
                buildings=buildings,
                row_boxes=row_boxes,
                keepouts=keepouts,
            ):
                chosen = fallback
            else:
                chosen = (2.4, min(h - 2.4, max(2.4, h * 0.45)))
        eq.x, eq.y = float(chosen[0]), float(chosen[1])
        placed.append(eq)
        occupied.append((eq.x, eq.y))
    plan.equipment = placed + others


def _nudge_off_stalls(
    x: float,
    y: float,
    *,
    w: float,
    h: float,
    buildings: list[Aabb],
    row_boxes: list[Aabb],
    keepouts: list[Aabb] | None = None,
) -> tuple[float, float]:
    """若仍压车位或出入口，向最近空位挪。"""
    if _feeder_point_ok(
        x, y, w=w, h=h, buildings=buildings, row_boxes=row_boxes, keepouts=keepouts
    ):
        return x, y
    found = _search_free_feeder_point(
        seed=(x, y),
        w=w,
        h=h,
        buildings=buildings,
        row_boxes=row_boxes,
        occupied=[],
        keepouts=keepouts,
    )
    return found if found is not None else (x, y)


def _clamp_equipment(plan: EvChargingStationPlan) -> None:
    """避让车位/建筑/出入口/过道；配电室内的箱变不要被推到室外。"""
    w, h = float(plan.site.widthM), float(plan.site.heightM)
    row_boxes = [(r, _row_aabb(r)) for r in plan.parkingRows]
    room_boxes = [_building_aabb(b) for b in _electrical_rooms(plan)]
    buildings = [
        b
        for b in _building_boxes(plan)
        if not any(abs(b[0] - r[0]) < 0.05 and abs(b[1] - r[1]) < 0.05 for r in room_boxes)
    ]
    all_rows = [rb for _, rb in row_boxes]
    gates = _gate_throat_keepouts(plan)
    aisles = (
        _aisle_keepouts(plan)
        + _gate_spine_keepouts(plan)
        + _row_opening_keepouts(plan)
        + _between_row_drive_keepouts(plan)
    )
    soft = gates + aisles

    def _in_room(x: float, y: float) -> bool:
        return any(
            rb[0] + 0.15 <= x <= rb[2] - 0.15 and rb[1] + 0.15 <= y <= rb[3] - 0.15
            for rb in room_boxes
        )

    for eq in plan.equipment:
        if eq.type in _FEEDER_TYPES and _in_room(float(eq.x), float(eq.y)):
            continue
        eq.x = min(max(float(eq.x), 2.0), w - 2.0)
        eq.y = min(max(float(eq.y), 2.0), h - 2.0)
        for _ in range(20):
            box = _eq_box(eq.x, eq.y)
            hit_b = next((b for b in buildings if _overlap(box, b, pad=-0.2)), None)
            if hit_b is not None:
                east = hit_b[2] + 2.8
                north = hit_b[3] + 2.8
                south = hit_b[1] - 2.8
                if east <= w - 2.5:
                    eq.x = east
                elif north <= h - 2.5:
                    eq.y = north
                elif south >= 2.5:
                    eq.y = south
                else:
                    eq.x = max(2.5, hit_b[0] - 2.8)
                eq.x = min(max(eq.x, 2.0), w - 2.0)
                eq.y = min(max(eq.y, 2.0), h - 2.0)
                continue
            hit_row = next((r for r, rb in row_boxes if _overlap(box, rb, pad=-0.2)), None)
            if hit_row is not None:
                rb = _row_aabb(hit_row)
                # 推到包围盒外四向，选仍靠边缘且空闲的点
                probes = [
                    (rb[2] + 2.8, eq.y),
                    (rb[0] - 2.8, eq.y),
                    (eq.x, rb[3] + 2.8),
                    (eq.x, rb[1] - 2.8),
                    (rb[2] + 2.8, rb[3] + 2.8),
                    (rb[0] - 2.8, rb[1] - 2.8),
                    (rb[2] + 2.8, rb[1] - 2.8),
                    (rb[0] - 2.8, rb[3] + 2.8),
                ]
                probes = sorted(
                    probes,
                    key=lambda p: (
                        0
                        if _feeder_point_ok(
                            p[0],
                            p[1],
                            w=w,
                            h=h,
                            buildings=buildings,
                            row_boxes=all_rows,
                            keepouts=soft,
                        )
                        else 1,
                        1 if _is_site_central(p[0], p[1], w, h) else 0,
                        min(p[0], w - p[0], p[1], h - p[1]) * -1,
                        abs(p[0] - eq.x) + abs(p[1] - eq.y),
                    ),
                )
                eq.x, eq.y = probes[0]
                eq.x = min(max(eq.x, 2.0), w - 2.0)
                eq.y = min(max(eq.y, 2.0), h - 2.0)
                continue
            hit_aisle = next((b for b in aisles if _overlap(box, b, pad=-0.2)), None)
            if hit_aisle is not None:
                found = _search_free_feeder_point(
                    seed=(eq.x, eq.y),
                    w=w,
                    h=h,
                    buildings=buildings,
                    row_boxes=all_rows,
                    occupied=[],
                    keepouts=soft,
                )
                if found is not None:
                    eq.x, eq.y = found
                    continue
                hx = 0.5 * (hit_aisle[0] + hit_aisle[2])
                hy = 0.5 * (hit_aisle[1] + hit_aisle[3])
                if abs(eq.x - hx) >= abs(eq.y - hy):
                    eq.x = hit_aisle[0] - 2.8 if eq.x <= hx else hit_aisle[2] + 2.8
                else:
                    eq.y = hit_aisle[1] - 2.8 if eq.y <= hy else hit_aisle[3] + 2.8
                eq.x = min(max(eq.x, 2.0), w - 2.0)
                eq.y = min(max(eq.y, 2.0), h - 2.0)
                continue
            hit = next((b for b in gates if _overlap(box, b, pad=-0.2)), None)
            if hit is None:
                break
            # 整条出入口边：推到禁区内侧（远离大门），不要滑到门洞两侧。
            if hit[1] <= 1.0:
                eq.y = hit[3] + 2.5
            elif hit[3] >= h - 1.0:
                eq.y = hit[1] - 2.5
            elif hit[0] <= 1.0:
                eq.x = hit[2] + 2.5
            else:
                eq.x = hit[0] - 2.5
            eq.x = min(max(eq.x, 2.0), w - 2.0)
            eq.y = min(max(eq.y, 2.0), h - 2.0)
        eq.x, eq.y = _nudge_off_stalls(
            eq.x,
            eq.y,
            w=w,
            h=h,
            buildings=buildings,
            row_boxes=all_rows,
            keepouts=soft,
        )


def _points_to_poly(pts: list[tuple[float, float]]) -> list[PointM]:
    out: list[PointM] = []
    for x, y in pts:
        if out and abs(out[-1].x - x) < 0.05 and abs(out[-1].y - y) < 0.05:
            continue
        out.append(PointM(x=x, y=y))
    if len(out) == 1:
        out.append(PointM(x=out[0].x + 0.4, y=out[0].y))
    return out


def _ensure_cables_on_chargers(plan: EvChargingStationPlan) -> None:
    hv = [c for c in plan.cables if c.voltage == "10kv"]
    width_mm, height_mm = 1200.0, 1100.0
    if plan.trenches:
        width_mm = float(plan.trenches[0].widthMm)
        height_mm = float(plan.trenches[0].heightMm)
    lv: list[CableSpec] = []
    trenches: list[TrenchSpec] = []
    for i, row in enumerate(plan.parkingRows):
        heads = charger_heads(row)
        if not heads:
            continue
        poly = _points_to_poly(heads)
        lv.append(CableSpec(id=f"lv_row{i}", voltage="0.4kv", polyline=poly, label=""))
        trenches.append(
            TrenchSpec(
                id=f"tr_row{i}",
                polyline=poly,
                widthMm=width_mm,
                heightMm=height_mm,
                lengthM=round(
                    sum(
                        math.hypot(b.x - a.x, b.y - a.y)
                        for a, b in zip(poly, poly[1:])
                    ),
                    1,
                ),
                label=f"电缆沟 {width_mm:g}(W)x{height_mm:g}(H)" if i == 0 else "",
            )
        )
    if lv:
        plan.cables = hv + lv
        plan.trenches = trenches


def _translate_plan(plan: EvChargingStationPlan, dx: float, dy: float) -> None:
    if abs(dx) < 0.02 and abs(dy) < 0.02:
        return
    for p in plan.site.polygon:
        p.x += dx
        p.y += dy
    for b in plan.buildings:
        b.rect.x += dx
        b.rect.y += dy
    for row in plan.parkingRows:
        row.origin = PointM(x=row.origin.x + dx, y=row.origin.y + dy)
    for eq in plan.equipment:
        eq.x += dx
        eq.y += dy
    for tree in plan.trees:
        tree.x += dx
        tree.y += dy
    for g in list(plan.greenery) + list(plan.roads):
        for p in g.polygon:
            p.x += dx
            p.y += dy
    for cable in plan.cables:
        for p in cable.polyline:
            p.x += dx
            p.y += dy
    for trench in plan.trenches:
        for p in trench.polyline:
            p.x += dx
            p.y += dy
    moved: list[GateSpec] = []
    for gate in collect_site_gates(plan.site):
        if gate.side in {"north", "south"}:
            moved.append(gate.model_copy(update={"offsetM": float(gate.offsetM) + dx}))
        else:
            moved.append(gate.model_copy(update={"offsetM": float(gate.offsetM) + dy}))
    if moved:
        set_site_gates(plan.site, moved)


def _sync_site_from_polygon(plan: EvChargingStationPlan) -> None:
    poly = [(p.x, p.y) for p in (plan.site.polygon or [])]
    if len(poly) < 3:
        return
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    _translate_plan(plan, -min(xs), -min(ys))
    xs = [p.x for p in plan.site.polygon]
    ys = [p.y for p in plan.site.polygon]
    plan.site.widthM = max(8.0, max(xs))
    plan.site.heightM = max(8.0, max(ys))


def _clip_gate_greenery(plan: EvChargingStationPlan) -> None:
    w, h = float(plan.site.widthM), float(plan.site.heightM)
    keepouts: list[Aabb] = list(_aisle_keepouts(plan))
    for gate in collect_site_gates(plan.site):
        if gate.side == "south":
            keepouts.append((gate.offsetM - 1.0, 0.0, gate.offsetM + gate.widthM + 1.0, 10.0))
        elif gate.side == "north":
            keepouts.append((gate.offsetM - 1.0, h - 10.0, gate.offsetM + gate.widthM + 1.0, h))
        elif gate.side == "west":
            keepouts.append((0.0, gate.offsetM - 1.0, 10.0, gate.offsetM + gate.widthM + 1.0))
        else:
            keepouts.append((w - 10.0, gate.offsetM - 1.0, w, gate.offsetM + gate.widthM + 1.0))
    if not keepouts:
        return
    kept = []
    for item in plan.greenery:
        box = _poly_aabb(item.polygon)
        if box is None:
            continue
        if any(_overlap(box, k) for k in keepouts):
            continue
        kept.append(item)
    plan.greenery = kept
    plan.trees = [
        t
        for t in plan.trees
        if not any(_overlap((t.x - 0.8, t.y - 0.8, t.x + 0.8, t.y + 0.8), k, pad=-0.1) for k in keepouts)
    ]


def _ensure_aisles(plan: EvChargingStationPlan) -> None:
    """开口侧回转车道。对侧分列时合成一条对准出入口的直线过道，不画半圆。"""
    grouped: dict[str, list[tuple[ParkingRowSpec, Aabb, bool]]] = {
        "west": [],
        "east": [],
        "north": [],
        "south": [],
        "other": [],
    }
    for row in plan.parkingRows:
        stalls = expand_stalls(row)
        if not stalls:
            continue
        boxes = [_aabb(rect_corners(rect)) for rect, _ in stalls]
        box = (
            min(b[0] for b in boxes),
            min(b[1] for b in boxes),
            max(b[2] for b in boxes),
            max(b[3] for b in boxes),
        )
        wall = _infer_row_wall(plan, row) or "other"
        grouped.setdefault(wall, []).append((row, box, _is_truck_row(row)))

    aisles: list[AisleSpec] = []
    used: set[str] = set()
    w, h = float(plan.site.widthM), float(plan.site.heightM)
    gates = collect_site_gates(plan.site)
    gate = gates[0] if gates else None

    if grouped["west"] and grouped["east"]:
        west_box = _merge_boxes([b for _, b, _ in grouped["west"]])
        east_box = _merge_boxes([b for _, b, _ in grouped["east"]])
        truck = any(t for _, _, t in grouped["west"] + grouped["east"])
        x0, x1 = west_box[2], east_box[0]
        y0 = min(west_box[1], east_box[1])
        y1 = max(west_box[3], east_box[3])
        if x1 - x0 >= 3.5:
            cx = 0.5 * (x0 + x1)
            width = min(max(TRUCK_AISLE_M if truck else CAR_AISLE_M, 3.5), x1 - x0)
            clear = min((x1 - x0) * 0.5 - 0.05, width * 0.5 + 0.25)
            clear = max(0.8, clear)
            cx = min(max(cx, x0 + clear), x1 - clear)
            y_lo, y_hi = min(y0, y1), max(y0, y1)
            gx = None
            need = TRUCK_AISLE_M if truck else CAR_AISLE_M
            if gate is not None and str(gate.side) in {"south", "north"}:
                gx = float(gate.offsetM) + float(gate.widthM) / 2.0
                gy = 1.2 if str(gate.side) == "south" else h - 1.2
                if str(gate.side) == "south" and not grouped["south"]:
                    y_lo = min(y_lo, gy)
                elif str(gate.side) == "north" and not grouped["north"]:
                    y_hi = max(y_hi, gy)
            if gx is not None and _circulation_first(plan):
                lo = x0 + need * 0.45
                hi = x1 - need * 0.45
                if hi > lo:
                    cx = min(max(gx, lo), hi)
            line = [PointM(x=cx, y=y_lo), PointM(x=cx, y=y_hi)]
            aisles.append(
                AisleSpec(
                    id="aisle_drive",
                    centerline=line,
                    widthM=max(3.5, width),
                    kind="drive",
                    label=f"{'重卡' if truck else '轿车'}回转车道≥{need:g}m",
                )
            )
            if gx is not None and abs(gx - cx) > 1.6 and not _circulation_first(plan):
                if str(gate.side) == "south":
                    if grouped["south"]:
                        sb = _merge_boxes([b for _, b, _ in grouped["south"]])
                        y_join = sb[3] + need * 0.5
                    else:
                        we_y0 = min(west_box[1], east_box[1])
                        y_join = max(need * 0.5, min(we_y0 - need * 0.5, 3.5))
                        y_join = max(need * 0.5, y_join)
                else:
                    if grouped["north"]:
                        nb = _merge_boxes([b for _, b, _ in grouped["north"]])
                        y_join = nb[1] - need * 0.5
                    else:
                        y_join = min(h - need * 0.5, max(h - 3.5, h - 1.2))
                aisles.append(
                    AisleSpec(
                        id="aisle_gate",
                        centerline=[PointM(x=gx, y=y_join), PointM(x=cx, y=y_join)],
                        widthM=need,
                        kind="drive",
                        label="",
                    )
                )
            used.update({"west", "east"})

    inner = bool(grouped["other"])
    if grouped["north"] and grouped["south"] and "west" not in used and not inner:
        north_box = _merge_boxes([b for _, b, _ in grouped["north"]])
        south_box = _merge_boxes([b for _, b, _ in grouped["south"]])
        truck = any(t for _, _, t in grouped["north"] + grouped["south"])
        y0, y1 = south_box[3], north_box[1]
        x0 = min(north_box[0], south_box[0])
        x1 = max(north_box[2], south_box[2])
        if y1 - y0 >= 3.5:
            cy = 0.5 * (y0 + y1)
            width = min(max(TRUCK_AISLE_M if truck else CAR_AISLE_M, 3.5), y1 - y0)
            clear = min((y1 - y0) * 0.5 - 0.05, width * 0.5 + 0.25)
            clear = max(0.8, clear)
            cy = min(max(cy, y0 + clear), y1 - clear)
            x_lo, x_hi = min(x0, x1), max(x0, x1)
            gy = None
            need = TRUCK_AISLE_M if truck else CAR_AISLE_M
            if gate is not None and str(gate.side) in {"west", "east"}:
                gy = float(gate.offsetM) + float(gate.widthM) / 2.0
                gx = 1.2 if str(gate.side) == "west" else w - 1.2
                if str(gate.side) == "west" and not grouped["west"]:
                    x_lo = min(x_lo, gx)
                elif str(gate.side) == "east" and not grouped["east"]:
                    x_hi = max(x_hi, gx)
            line = [PointM(x=x_lo, y=cy), PointM(x=x_hi, y=cy)]
            aisles.append(
                AisleSpec(
                    id="aisle_drive",
                    centerline=line,
                    widthM=max(3.5, width),
                    kind="drive",
                    label=f"{'重卡' if truck else '轿车'}回转车道≥{need:g}m",
                )
            )
            if gy is not None and abs(gy - cy) > 1.6 and not _circulation_first(plan):
                if str(gate.side) == "west":
                    if grouped["west"]:
                        wb = _merge_boxes([b for _, b, _ in grouped["west"]])
                        x_join = wb[2] + need * 0.5
                    else:
                        x_join = max(1.2, min(3.5, need * 0.5))
                else:
                    if grouped["east"]:
                        eb = _merge_boxes([b for _, b, _ in grouped["east"]])
                        x_join = eb[0] - need * 0.5
                    else:
                        x_join = min(w - 1.2, max(w - 3.5, w - need * 0.5))
                aisles.append(
                    AisleSpec(
                        id="aisle_gate",
                        centerline=[PointM(x=x_join, y=gy), PointM(x=x_join, y=cy)],
                        widthM=need,
                        kind="drive",
                        label="",
                    )
                )
            used.update({"north", "south"})

    for key, rows in grouped.items():
        if key in used:
            continue
        for row, box, truck in rows:
            aisles.append(_aisle_for_row(plan, row, box, truck, gate=gate))
    _append_gate_spine(plan, aisles, grouped, gate)
    plan.aisles = aisles[:40]
    _keep_aisles_off_stalls(plan)


def _merge_boxes(boxes: list[Aabb]) -> Aabb:
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


def _aisle_aabb(aisle: AisleSpec) -> Aabb | None:
    pts = [(float(p.x), float(p.y)) for p in aisle.centerline]
    if len(pts) < 2:
        return None
    half = max(0.6, float(aisle.widthM) / 2.0)
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    vertical = abs(xs[0] - xs[-1]) <= abs(ys[0] - ys[-1]) + 0.05
    if vertical:
        x = sum(xs) / len(xs)
        return (x - half, min(ys), x + half, max(ys))
    y = sum(ys) / len(ys)
    return (min(xs), y - half, max(xs), y + half)


def _keep_aisles_off_stalls(plan: EvChargingStationPlan) -> None:
    """车道带不得压进车位。优先把中心线挪进空隙，宽度不得压到建议值以下。"""
    stall_boxes = [_row_aabb(r) for r in plan.parkingRows]
    if not stall_boxes:
        return
    for aisle in plan.aisles:
        if str(aisle.id) in {"aisle_gate", "aisle_drive"}:
            continue
        box = _aisle_aabb(aisle)
        if box is None or not any(_overlap(box, s, pad=-0.15) for s in stall_boxes):
            continue
        pts = list(aisle.centerline)
        if len(pts) < 2:
            continue
        min_w = TRUCK_AISLE_M if "重卡" in (aisle.label or "") else CAR_AISLE_M
        vertical = abs(pts[0].x - pts[-1].x) <= abs(pts[0].y - pts[-1].y) + 0.05
        mid_x = (box[0] + box[2]) * 0.5
        if vertical:
            hit = next(
                (s for s in stall_boxes if s[0] < mid_x < s[2] and s[1] < box[3] and s[3] > box[1]),
                None,
            )
            if hit is not None:
                west_lim = max((s[2] for s in stall_boxes if s[2] <= hit[0] + 0.2), default=0.8)
                east_lim = min((s[0] for s in stall_boxes if s[0] >= hit[2] - 0.2), default=box[2] + 40)
                left_gap = hit[0] - west_lim
                right_gap = east_lim - hit[2]
                if right_gap >= left_gap and right_gap >= min_w:
                    aisle.widthM = min(float(aisle.widthM), max(min_w, right_gap - 0.5))
                    cx = hit[2] + right_gap * 0.5
                    for p in pts:
                        p.x = cx
                elif left_gap >= min_w:
                    aisle.widthM = min(float(aisle.widthM), max(min_w, left_gap - 0.5))
                    cx = hit[0] - left_gap * 0.5
                    for p in pts:
                        p.x = cx
                else:
                    aisle.widthM = max(min_w, float(aisle.widthM))
                cx = max(min_w * 0.5, min(float(plan.site.widthM) - min_w * 0.5, pts[0].x))
                for p in pts:
                    p.x = cx
                continue
            west_edge = max((s[2] for s in stall_boxes if s[2] <= mid_x), default=None)
            east_edge = min((s[0] for s in stall_boxes if s[0] >= mid_x), default=None)
            if west_edge is not None and east_edge is not None and east_edge - west_edge >= min_w:
                gap = east_edge - west_edge
                width = min(float(aisle.widthM), max(min_w, gap - 0.5))
                cx = 0.5 * (west_edge + east_edge)
                aisle.widthM = width
                for p in pts:
                    p.x = cx
            else:
                aisle.widthM = max(min_w, float(aisle.widthM))
        else:
            south_edge = max((s[3] for s in stall_boxes if s[3] <= (box[1] + box[3]) * 0.5), default=None)
            north_edge = min((s[1] for s in stall_boxes if s[1] >= (box[1] + box[3]) * 0.5), default=None)
            if south_edge is not None and north_edge is not None and north_edge - south_edge >= min_w:
                gap = north_edge - south_edge
                width = min(float(aisle.widthM), max(min_w, gap - 0.5))
                cy = 0.5 * (south_edge + north_edge)
                aisle.widthM = width
                for p in pts:
                    p.y = cy
            else:
                aisle.widthM = max(min_w, float(aisle.widthM))
    _snap_gate_aisle_to_drive(plan)


def _snap_gate_aisle_to_drive(plan: EvChargingStationPlan) -> None:
    drive = next((a for a in plan.aisles if str(a.id) == "aisle_drive"), None)
    gate_a = next((a for a in plan.aisles if str(a.id) == "aisle_gate"), None)
    if (
        drive is None
        or gate_a is None
        or len(drive.centerline) < 2
        or len(gate_a.centerline) < 2
    ):
        return
    d0, d1 = drive.centerline[0], drive.centerline[-1]
    g0, g1 = gate_a.centerline[0], gate_a.centerline[-1]
    vertical_drive = abs(d0.x - d1.x) <= abs(d0.y - d1.y) + 0.05
    if vertical_drive and abs(g0.y - g1.y) < abs(g0.x - g1.x):
        cx = 0.5 * (d0.x + d1.x)
        far = g0 if abs(g0.x - cx) <= abs(g1.x - cx) else g1
        far.x = cx


def _gate_anchor(gate: GateSpec, w: float, h: float) -> tuple[float, float]:
    return {
        "south": (float(gate.offsetM) + float(gate.widthM) / 2.0, 1.2),
        "north": (float(gate.offsetM) + float(gate.widthM) / 2.0, h - 1.2),
        "west": (1.2, float(gate.offsetM) + float(gate.widthM) / 2.0),
        "east": (w - 1.2, float(gate.offsetM) + float(gate.widthM) / 2.0),
    }[str(gate.side)]


def _aisle_line_toward_gate(
    p_far: tuple[float, float],
    p_near: tuple[float, float],
    gate: GateSpec | None,
    w: float,
    h: float,
    *,
    align_to_gate: bool = True,
) -> list[PointM]:
    """过道中心线对准出入口：只走直线，不画圆弧半包。"""
    if gate is None:
        return [PointM(x=p_far[0], y=p_far[1]), PointM(x=p_near[0], y=p_near[1])]
    gx, gy = _gate_anchor(gate, w, h)
    vertical = abs(p_far[0] - p_near[0]) <= abs(p_far[1] - p_near[1]) + 0.05
    if vertical:
        x = p_near[0]
        if align_to_gate and str(gate.side) in {"south", "north"}:
            x = gx
        ys = [p_far[1], p_near[1]]
        if str(gate.side) in {"south", "north"}:
            ys.append(gy)
        return [PointM(x=x, y=min(ys)), PointM(x=x, y=max(ys))]
    y = p_near[1]
    if align_to_gate and str(gate.side) in {"west", "east"}:
        y = gy
    xs = [p_far[0], p_near[0]]
    if str(gate.side) in {"west", "east"}:
        xs.append(gx)
    return [PointM(x=min(xs), y=y), PointM(x=max(xs), y=y)]


def _append_gate_spine(
    plan: EvChargingStationPlan,
    aisles: list[AisleSpec],
    grouped: dict[str, list[tuple[ParkingRowSpec, Aabb, bool]]],
    gate: GateSpec | None,
) -> None:
    """对准出入口的贯通车道。小场地即使没有内排也要画出正对大门的路。"""
    if gate is None:
        return
    compact = _circulation_first(plan)
    has_drive = any(str(a.id) == "aisle_drive" for a in aisles)
    inner = grouped.get("other") or []
    if has_drive and not compact:
        return
    if not inner and not compact:
        return
    w, h = float(plan.site.widthM), float(plan.site.heightM)
    need = CAR_AISLE_M
    if any(t for rows in grouped.values() for _, _, t in rows):
        need = TRUCK_AISLE_M
    axis = _gate_spine_axis(plan)
    if compact and axis is not None:
        kind, mid, _half = axis
        if kind == "x":
            cx = mid
            y_lo, y_hi = 1.2, h - 1.2
            if has_drive:
                drive = next(a for a in aisles if str(a.id) == "aisle_drive")
                for p in drive.centerline:
                    p.x = cx
                aisles[:] = [a for a in aisles if str(a.id) != "aisle_gate"]
                return
            aisles.append(
                AisleSpec(
                    id="aisle_drive",
                    centerline=[PointM(x=cx, y=y_lo), PointM(x=cx, y=y_hi)],
                    widthM=need,
                    kind="drive",
                    label=f"轿车回转车道≥{need:g}m",
                )
            )
            return
        cy = mid
        x_lo, x_hi = 1.2, w - 1.2
        if has_drive:
            drive = next(a for a in aisles if str(a.id) == "aisle_drive")
            for p in drive.centerline:
                p.y = cy
            aisles[:] = [a for a in aisles if str(a.id) != "aisle_gate"]
            return
        aisles.append(
            AisleSpec(
                id="aisle_drive",
                centerline=[PointM(x=x_lo, y=cy), PointM(x=x_hi, y=cy)],
                widthM=need,
                kind="drive",
                label=f"轿车回转车道≥{need:g}m",
            )
        )
        return
    if has_drive or not inner or str(gate.side) not in {"south", "north"}:
        return
    gx = float(gate.offsetM) + float(gate.widthM) / 2.0
    eastish = gx >= w * 0.55
    x_edge = max(b[2] for _, b, _ in inner) if eastish else min(b[0] for _, b, _ in inner)
    if eastish:
        cx = min(w - need * 0.5, float(gate.offsetM) - 0.2, x_edge + need * 0.5)
        cx = max(need * 0.5, cx)
    else:
        cx = max(need * 0.5, float(gate.offsetM) + float(gate.widthM) + 0.2, x_edge - need * 0.5)
        cx = min(w - need * 0.5, cx)
    y_lo = min(b[3] for _, b, _ in grouped["south"]) if grouped["south"] else 1.2
    y_hi = max(b[1] for _, b, _ in grouped["north"]) if grouped["north"] else h - 1.2
    if y_hi - y_lo < 3.5:
        return
    aisles.append(
        AisleSpec(
            id="aisle_drive",
            centerline=[PointM(x=cx, y=y_lo), PointM(x=cx, y=y_hi)],
            widthM=need,
            kind="drive",
            label=f"轿车回转车道≥{need:g}m",
        )
    )
    if abs(gx - cx) > 1.6:
        y_join = y_lo + need * 0.5 if str(gate.side) == "south" else y_hi - need * 0.5
        if grouped["south"] and str(gate.side) == "south":
            y_join = min(b[3] for _, b, _ in grouped["south"]) + need * 0.5
        if grouped["north"] and str(gate.side) == "north":
            y_join = max(b[1] for _, b, _ in grouped["north"]) - need * 0.5
        aisles.append(
            AisleSpec(
                id="aisle_gate",
                centerline=[PointM(x=gx, y=y_join), PointM(x=cx, y=y_join)],
                widthM=need,
                kind="drive",
                label="",
            )
        )


def _aisle_for_row(
    plan: EvChargingStationPlan,
    row: ParkingRowSpec,
    box: Aabb,
    truck: bool,
    *,
    gate: GateSpec | None = None,
) -> AisleSpec:
    min_x, min_y, max_x, max_y = box
    width = TRUCK_AISLE_M if truck else CAR_AISLE_M
    label = f"{'重卡' if truck else '轿车'}回转车道≥{width:g}m"
    wall = _infer_row_wall(plan, row)
    w, h = float(plan.site.widthM), float(plan.site.heightM)
    ch_side = str(row.charger.side) if row.charger else ""
    if wall == "west" or (wall is None and row.along == "y" and min_x < w * 0.35):
        x = min(w - 0.5, max_x + width * 0.5 + 0.25)
        line = [PointM(x=x, y=min_y), PointM(x=x, y=max_y)]
    elif wall == "east":
        x = max(0.5, min_x - width * 0.5 - 0.25)
        line = [PointM(x=x, y=min_y), PointM(x=x, y=max_y)]
    elif wall == "north":
        y = max(0.5, min_y - width * 0.5 - 0.25)
        line = [PointM(x=min_x, y=y), PointM(x=max_x, y=y)]
    elif wall == "south":
        y = min(h - 0.5, max_y + width * 0.5 + 0.25)
        line = [PointM(x=min_x, y=y), PointM(x=max_x, y=y)]
    elif row.along == "x":
        if ch_side == "tail":
            y = min(h - 0.5, max_y + width * 0.5 + 0.25)
        else:
            y = max(0.5, min_y - width * 0.5 - 0.25)
        line = [PointM(x=min_x, y=y), PointM(x=max_x, y=y)]
    else:
        x = max(0.5, min_x - width * 0.55)
        line = [PointM(x=x, y=min_y), PointM(x=x, y=max_y)]
    if gate is not None and wall is not None and len(line) >= 2:
        stretched = _aisle_line_toward_gate(
            (line[0].x, line[0].y),
            (line[-1].x, line[-1].y),
            gate,
            w,
            h,
            align_to_gate=False,
        )
        if len(stretched) >= 2:
            line = stretched
    return AisleSpec(id=f"aisle_{row.id}", centerline=line, widthM=width, kind="drive", label=label)


_CHARGER_LEGEND = ("dc_320kw", "dc_160kw", "dc_120kw", "ac_14kw")
_CHARGER_LABELS = {
    "dc_320kw": "320kW直流充电桩",
    "dc_160kw": "160kW直流充电桩",
    "dc_120kw": "120kW直流充电桩",
    "ac_14kw": "14kW交流充电桩",
}


def sync_charger_annotations(plan: EvChargingStationPlan) -> None:
    """图例与桩旁文字跟各排 charger.type 走，避免改 320kW 后仍写 160kW/直流充电桩。"""
    for row in plan.parkingRows:
        kind = row.charger.type if row.charger else ""
        if kind in _CHARGER_LABELS:
            row.labelPrefix = _CHARGER_LABELS[kind]
    _ensure_legend_vehicles(plan)


def _ensure_legend_vehicles(plan: EvChargingStationPlan) -> None:
    items = [str(s) for s in (plan.legend or [])]
    items = [s for s in items if s not in _CHARGER_LEGEND]
    charger_types: list[str] = []
    for row in plan.parkingRows:
        kind = row.charger.type if row.charger else ""
        if kind in _CHARGER_LEGEND and kind not in charger_types:
            charger_types.append(kind)
    insert_at = items.index("box_transformer") + 1 if "box_transformer" in items else 0
    for i, kind in enumerate(charger_types):
        items.insert(insert_at + i, kind)
    has_car = any(not _is_truck_row(r) for r in plan.parkingRows)
    has_truck = any(_is_truck_row(r) for r in plan.parkingRows)
    if has_car and "parking" not in items:
        items.append("parking")
    if has_truck and "truck" not in items:
        if "parking" in items:
            items.insert(items.index("parking") + 1, "truck")
        else:
            items.append("truck")
    if any(eq.type == "group_host" for eq in plan.equipment) and "group_host" not in items:
        at = items.index("box_transformer") + 1 if "box_transformer" in items else 0
        items.insert(at, "group_host")
    plan.legend = items[:16]


def _finalize_plan_annotations(plan: EvChargingStationPlan) -> None:
    """桩编号、贴墙桩侧、开口侧车道、空白地设备、电缆沟。"""
    _bind_chargers_to_stalls(plan)
    _apply_wall_mount_chargers(plan)
    plan.aisles = []
    _ensure_aisles(plan)
    sync_charger_annotations(plan)
    _align_gate(plan)
    _strip_spurious_gate_labels(plan)
    _apply_blank_ground_equipment(plan)
    _clamp_equipment(plan)
    _ensure_cables_on_chargers(plan)
    _clip_gate_greenery(plan)


def prepare_plan(
    plan: EvChargingStationPlan,
    *,
    gentle: bool = False,
    lock_envelope: bool = False,
    query: str = "",
) -> EvChargingStationPlan:
    """出图前校正。gentle=True 用于修订：少整场重装，尽量保留已有排位。

    pack 之后必须再钉对侧围墙：PNG/SVG 会再次 prepare，否则东墙一排被挪到北墙，
    又变回北+西转角，过道也对不准出入口。
    编号在 _finalize 里定稿后，再按 query 涂桩型，避免「先改功率、再重编号」把 160kW 涂丢。
    """
    out = plan.model_copy(deep=True)
    _sync_site_from_polygon(out)
    saved_gates = _snapshot_gates(out)
    _drop_copied_yard_roads(out)
    out.parkingRows = [_normalize_row(row) for row in out.parkingRows]
    pack_parking(out, gentle=gentle)
    keep_types = False
    if query:
        from api.services.layouts.revise import _parse_stall_range

        keep_types = _parse_stall_range(query) is not None
    out = pin_rows_against_walls(
        out,
        lock_envelope=lock_envelope,
        gentle=gentle,
        preserve_rows=keep_types,
    )
    _finalize_plan_annotations(out)
    _restore_gates(out, saved_gates)
    if query:
        from api.services.layouts.revise import apply_query_charger_to_plan

        apply_query_charger_to_plan(out, query)
        sync_charger_annotations(out)
    return out


def touch_up_plan(
    plan: EvChargingStationPlan,
    *,
    lock_envelope: bool = False,
    gentle: bool = False,
    preserve_rows: bool = False,
) -> EvChargingStationPlan:
    """空间指令落地后的轻触：轻推越界后仍钉回对侧围墙，避免转角堵死端头车位。"""
    out = plan.model_copy(deep=True)
    saved_gates = _snapshot_gates(out)
    out.parkingRows = [_normalize_row(row) for row in out.parkingRows]
    if preserve_rows:
        _finalize_plan_annotations(out)
        _restore_gates(out, saved_gates)
        return out
    blocked = _building_boxes(out) + _road_boxes(out)
    _nudge_all_rows(out.parkingRows, out, blocked)
    _separate_overlapping_rows(out)
    out = pin_rows_against_walls(
        out, lock_envelope=lock_envelope, gentle=gentle, preserve_rows=preserve_rows
    )
    _finalize_plan_annotations(out)
    _restore_gates(out, saved_gates)
    return out
