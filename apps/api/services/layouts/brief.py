"""从用户话/读图里抽出规模，避免知识库案例把桩数和箱变整案覆盖。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from api.services.layouts.schema import (
    BuildingSpec,
    ChargerOnRow,
    EquipmentSpec,
    EvChargingStationPlan,
    GateSpec,
    ParkingRowSpec,
    PointM,
    RectM,
    clamp_gate_to_span,
    coerce_polyline_points,
    collect_site_gates,
    set_site_gates,
)

_FEEDER = {
    "ring_cabinet",
    "box_transformer",
    "ring_box_transformer",
    "lv_cabinet",
}

_NUM = r"(?:三十|二十|廿|十六|十五|十四|十三|十二|十一|十|[一两二三四五六七八九]|\d+)"
_CN_NUM = {
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
    "十一": 11,
    "十二": 12,
    "十三": 13,
    "十四": 14,
    "十五": 15,
    "十六": 16,
    "二十": 20,
    "廿": 20,
    "三十": 30,
}
_TRUCK_RE = re.compile(rf"({_NUM})\s*个\s*(?:重卡|货车|大车|卡车)")
_CAR_RE = re.compile(
    rf"({_NUM})\s*(?:个|台)(?:\s*带充电桩的)?(?:桥车|轿车|小车|乘用车|客车|微型车)"
)
_STALL_RE = re.compile(rf"({_NUM})\s*个(?:\s*带充电桩的)?车位")
_PILE_RE = re.compile(
    rf"({_NUM})\s*(?:个|台).{{0,16}}?(?:充电)?桩"
)
_AREA_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:㎡|m²|m2|平方米|平米)", re.I)
_KVA_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:kva|千伏安)", re.I)
_W_TX_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:kW|W|瓦)\s*的?\s*(?:箱变|变电|变压器)",
    re.I,
)
_TX_N_RE = re.compile(
    rf"({_NUM})\s*台\s*(?:(?P<cap>\d+(?:\.\d+)?)\s*(?:kva|kw|w|千伏安|瓦))?\s*的?(?:箱变|变电|变压器)",
    re.I,
)
_HOST_N_RE = re.compile(
    rf"({_NUM})\s*台\s*(?:\d+(?:\.\d+)?\s*(?:kw|千瓦))?\s*的?(?:群冲|群充)?\s*(?:主机柜|充电主机)",
    re.I,
)
_HOST_KW_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:kw|千瓦)\s*的?(?:群冲|群充)",
    re.I,
)
_ROOM_RE = re.compile(r"配电室|变电所|配电房|配电间")
_ROOM_LABELS = ("配电室", "变电所", "配电房", "配电间")
_SITE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*m\s*[x×*]\s*(\d+(?:\.\d+)?)\s*m", re.I)
_SITE_CN_RE = re.compile(
    r"长\s*(\d+(?:\.\d+)?)\s*米.{0,12}宽\s*(\d+(?:\.\d+)?)\s*米"
)
_SITE_EW_NS_RE = re.compile(
    r"东西.{0,6}?(\d+(?:\.\d+)?)\s*米.{0,16}?南北.{0,6}?(\d+(?:\.\d+)?)\s*米"
)
_SITE_NS_EW_RE = re.compile(
    r"南北.{0,6}?(\d+(?:\.\d+)?)\s*米.{0,16}?东西.{0,6}?(\d+(?:\.\d+)?)\s*米"
)
_YARD_HINT_RE = re.compile(r"(空地|待办(?:场地)?|右侧场地|东侧场地|待办空地)")
_XY_PAIR_RE = re.compile(r"\(\s*(-?\d+(?:\.\d+)?)\s*[,，]\s*(-?\d+(?:\.\d+)?)\s*\)")


@dataclass(frozen=True)
class LayoutBrief:
    trucks: int | None = None
    cars: int | None = None
    transformer_n: int | None = None
    transformer_kva: float | None = None
    site_w: float | None = None
    site_h: float | None = None
    site_area_m2: float | None = None
    gate_side: str | None = None
    gate_east: bool = False
    gate_along: str | None = None  # east|west|north|south|center
    site_is_yard: bool = False
    electrical_room: bool = False
    host_n: int | None = None
    host_kw: float | None = None

    def has_scale(self) -> bool:
        return any(
            v is not None
            for v in (
                self.trucks,
                self.cars,
                self.transformer_n,
                self.transformer_kva,
                self.gate_side,
                self.site_w,
                self.site_h,
                self.site_area_m2,
                self.host_n,
                self.host_kw,
            )
        ) or self.electrical_room


def parse_layout_brief(*texts: str) -> LayoutBrief:
    blob = "\n".join(t for t in texts if t).strip()
    if not blob:
        return LayoutBrief()
    trucks = _first_count(_TRUCK_RE, blob)
    cars = _first_count(_CAR_RE, blob)
    if cars is None and trucks is None:
        cars = _first_count(_STALL_RE, blob)
    if cars is None and trucks is None:
        cars = _first_count(_PILE_RE, blob)
    tx_n = _first_count(_TX_N_RE, blob)
    kva = None
    for mk in _KVA_RE.finditer(blob):
        after = blob[mk.end() : mk.end() + 10]
        if any(tok in after for tok in ("群冲", "群充", "主机柜")):
            continue
        kva = float(mk.group(1))
        break
    mtx = _TX_N_RE.search(blob)
    if kva is None and mtx:
        cap = mtx.groupdict().get("cap") if mtx.groupdict() else None
        if cap:
            kva = float(cap)
    if kva is None:
        mw = _W_TX_RE.search(blob)
        if mw:
            raw = float(mw.group(1))
            token = mw.group(0).lower()
            # 「2000W箱变」口语几乎总是指 2000kVA，不当成 2kW
            if "kw" in token or "千瓦" in token:
                kva = raw
            elif raw >= 100:
                kva = raw
            else:
                kva = raw / 1000.0
    if kva is not None and tx_n is None:
        tx_n = 1
    host_n = _first_count(_HOST_N_RE, blob)
    host_kw = None
    mh = _HOST_KW_RE.search(blob)
    if mh:
        host_kw = float(mh.group(1))
        if host_n is None:
            host_n = 1
    electrical_room = bool(_ROOM_RE.search(blob))
    site_w = site_h = None
    site_area_m2 = None
    site_is_yard = False
    ms_ew = _SITE_EW_NS_RE.search(blob)
    ms_ns = _SITE_NS_EW_RE.search(blob)
    if ms_ew:
        site_w, site_h = float(ms_ew.group(1)), float(ms_ew.group(2))
    elif ms_ns:
        site_h, site_w = float(ms_ns.group(1)), float(ms_ns.group(2))
    ms = _SITE_RE.search(blob)
    if site_w is None and ms:
        site_w, site_h = float(ms.group(1)), float(ms.group(2))
        window = blob[max(0, ms.start() - 16) : ms.end() + 8]
        site_is_yard = bool(_YARD_HINT_RE.search(window))
    elif site_w is None:
        ms_cn = _SITE_CN_RE.search(blob)
        if ms_cn:
            site_w, site_h = float(ms_cn.group(1)), float(ms_cn.group(2))
        elif any(tok in blob for tok in ("足球场", "标准足球")):
            site_w, site_h = 105.0, 68.0
    ma = _AREA_RE.search(blob)
    if ma:
        site_area_m2 = float(ma.group(1))
        if site_area_m2 < 20 or site_area_m2 > 200_000:
            site_area_m2 = None
    gate_side, gate_along = _parse_gate_location(blob)
    gate_east = gate_along == "east"
    return LayoutBrief(
        trucks=trucks,
        cars=cars,
        transformer_n=tx_n,
        transformer_kva=kva,
        site_w=site_w,
        site_h=site_h,
        site_area_m2=site_area_m2,
        gate_side=gate_side,
        gate_east=gate_east,
        gate_along=gate_along,
        site_is_yard=site_is_yard,
        electrical_room=electrical_room,
        host_n=host_n,
        host_kw=host_kw,
    )


def apply_layout_brief(plan: EvChargingStationPlan, *texts: str) -> EvChargingStationPlan:
    brief = parse_layout_brief(*texts)
    if not brief.has_scale():
        return plan
    out = plan.model_copy(deep=True)
    if brief.site_area_m2 is not None and not (brief.site_w and brief.site_h):
        scale_site_to_area_m2(out, brief.site_area_m2)
    if brief.trucks is not None or brief.cars is not None:
        _replace_stall_mix(out, trucks=brief.trucks, cars=brief.cars)
    apply_equipment_additions(out, brief)
    if brief.gate_side:
        _apply_gate_hint(
            out, side=brief.gate_side, east=brief.gate_east, along=brief.gate_along
        )
    if brief.site_w and brief.site_h and not brief.site_is_yard:
        out.site.widthM = brief.site_w
        out.site.heightM = brief.site_h
        if len(out.site.polygon or []) < 3:
            out.site.polygon = []
    return out


def _shoelace_area(pts: list[tuple[float, float]]) -> float:
    if len(pts) < 3:
        return 0.0
    area = 0.0
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        area += x1 * y2 - x2 * y1
    return abs(area) * 0.5


def scale_site_to_area_m2(plan: EvChargingStationPlan, area_m2: float) -> None:
    """把红线/矩形包络缩放到目标面积，保持外形比例（草稿 260㎡ 等）。"""
    target = float(area_m2)
    if target < 20:
        return
    poly = [(p.x, p.y) for p in (plan.site.polygon or [])]
    if len(poly) >= 3:
        cur = _shoelace_area(poly)
        if cur < 1.0:
            return
        factor = (target / cur) ** 0.5
        cx = sum(p[0] for p in poly) / len(poly)
        cy = sum(p[1] for p in poly) / len(poly)

        def _sx(x: float, y: float) -> tuple[float, float]:
            return (cx + (x - cx) * factor, cy + (y - cy) * factor)

        plan.site.polygon = [PointM(x=x, y=y) for x, y in (_sx(p[0], p[1]) for p in poly)]
        xs = [p.x for p in plan.site.polygon]
        ys = [p.y for p in plan.site.polygon]
        # 平移到西南角贴近原点，便于绘图
        min_x, min_y = min(xs), min(ys)
        plan.site.polygon = [
            PointM(x=p.x - min_x, y=p.y - min_y) for p in plan.site.polygon
        ]
        plan.site.widthM = max(8.0, max(p.x for p in plan.site.polygon))
        plan.site.heightM = max(8.0, max(p.y for p in plan.site.polygon))
        for b in plan.buildings:
            r = b.rect
            x0, y0 = _sx(r.x, r.y)
            x1, y1 = _sx(r.x + r.w, r.y + r.h)
            b.rect.x = min(x0, x1) - min_x
            b.rect.y = min(y0, y1) - min_y
            b.rect.w = abs(x1 - x0)
            b.rect.h = abs(y1 - y0)
        for row in plan.parkingRows:
            ox, oy = _sx(row.origin.x, row.origin.y)
            row.origin = PointM(x=ox - min_x, y=oy - min_y)
        for eq in plan.equipment:
            ex, ey = _sx(eq.x, eq.y)
            eq.x, eq.y = ex - min_x, ey - min_y
        return
    # 无 polygon：按面积改矩形，尽量保留原长宽比
    w, h = float(plan.site.widthM), float(plan.site.heightM)
    cur = max(1.0, w * h)
    factor = (target / cur) ** 0.5
    plan.site.widthM = max(8.0, w * factor)
    plan.site.heightM = max(8.0, h * factor)


def restore_draft_context(
    plan: EvChargingStationPlan,
    *texts: str,
    has_draft_image: bool = False,
) -> EvChargingStationPlan:
    """不再补厂房/办公楼/路模板；建筑和道路以本轮 JSON 为准。"""
    del texts, has_draft_image
    return plan


_DEFAULT_COPIED_BUILDINGS = {"厂房", "办公楼"}


def prune_unmentioned_site_context(
    plan: EvChargingStationPlan, *texts: str
) -> EvChargingStationPlan:
    """丢掉示例/上一轮拷来的厂房办公楼和中间路，除非本轮原文或读图里写了。"""
    blob = "\n".join(t for t in texts if t)
    out = plan.model_copy(deep=True)
    kept: list[BuildingSpec] = []
    for b in out.buildings:
        label = (b.label or "").strip()
        if label in _DEFAULT_COPIED_BUILDINGS and label not in blob:
            continue
        kept.append(b)
    out.buildings = kept
    if "路" not in blob and "道路" not in blob and "马路" not in blob:
        out.roads = [r for r in out.roads if (r.label or "").strip() != "路"]
    return out


def parse_site_polygon(*texts: str) -> list[dict[str, float]]:
    blob = "\n".join(t for t in texts if t)
    if not blob:
        return []
    # 读图常写成 polygon: [...] 或 site.polygon；也兼容整段 JSON 里的 site.polygon。
    for m in re.finditer(
        r"(?:site\s*\.\s*)?polygon\s*[:=]\s*(\[(?:[^\[\]]|\[[^\[\]]*\]){8,}\])",
        blob,
        flags=re.I,
    ):
        try:
            data = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        pts = coerce_polyline_points(data)
        if pts and len(pts) >= 3:
            return pts
    for m in re.finditer(r"\{[^{}]{20,}\}", blob):
        raw = m.group(0)
        if "polygon" not in raw.lower():
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            site = data.get("site") if isinstance(data.get("site"), dict) else data
            pts = coerce_polyline_points(
                (site or {}).get("polygon") if isinstance(site, dict) else None
            )
            if pts and len(pts) >= 3:
                return pts
    for m in re.finditer(r"\[(?:[^\[\]]|\[[^\[\]]*\]){8,}\]", blob):
        raw = m.group(0)
        if "x" not in raw.lower() and "X" not in raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        pts = coerce_polyline_points(data)
        if pts and len(pts) >= 3:
            return pts
    pairs = _XY_PAIR_RE.findall(blob)
    if len(pairs) >= 4:
        return [{"x": float(a), "y": float(b)} for a, b in pairs[:40]]
    return []


def attach_site_polygon(plan: EvChargingStationPlan, *texts: str) -> EvChargingStationPlan:
    """读图/用户给出的红线优先于模型臆造的 site.polygon。"""
    out = plan.model_copy(deep=True)
    pts = parse_site_polygon(*texts)
    if len(pts) < 3:
        return out
    out.site.polygon = [PointM(x=p["x"], y=p["y"]) for p in pts]
    xs = [p["x"] for p in pts]
    ys = [p["y"] for p in pts]
    # 包络跟读图顶点走，不要保留模型瞎编的大矩形 width/height
    out.site.widthM = max(8.0, max(xs) - min(xs))
    out.site.heightM = max(8.0, max(ys) - min(ys))
    return out


def _first_count(cre: re.Pattern[str], blob: str) -> int | None:
    m = cre.search(blob)
    if not m:
        return None
    raw = str(m.group(1) or "").strip()
    if raw.isdigit():
        n = int(raw)
    else:
        n = _CN_NUM.get(raw)
        if n is None:
            return None
    if n < 1 or n > 80:
        return None
    return n


def _is_truck_spec(row: ParkingRowSpec) -> bool:
    return float(row.stallLengthM) >= 10 and float(row.stallWidthM) >= 4.6


def _resize_kind_rows(
    rows: list[ParkingRowSpec],
    count: int,
    *,
    make: ParkingRowSpec,
) -> list[ParkingRowSpec]:
    if count <= 0:
        return []
    if not rows:
        make.stalls = count
        return [make]
    # 模型常吐出很多排；目标车位更少时不能再「每排至少 1」否则 leftover 变负
    if len(rows) > count:
        head = rows[0].model_copy(deep=True)
        head.stalls = count
        return [head]
    total = sum(max(1, int(r.stalls)) for r in rows) or len(rows)
    leftover = count
    out: list[ParkingRowSpec] = []
    for i, row in enumerate(rows):
        remain_after = len(rows) - i - 1
        if remain_after == 0:
            row.stalls = max(1, leftover)
        else:
            part = max(1, round(count * max(1, int(row.stalls)) / total))
            part = min(part, max(1, leftover - remain_after))
            row.stalls = part
            leftover -= row.stalls
        out.append(row)
    return out


def _replace_stall_mix(
    plan: EvChargingStationPlan, *, trucks: int | None, cars: int | None
) -> None:
    """只改车位数量，尽量沿用模型给的 origin / 角度。"""
    if trucks is None and cars is None:
        return
    existing = list(plan.parkingRows)
    truck_rows = [r for r in existing if _is_truck_spec(r)]
    car_rows = [r for r in existing if not _is_truck_spec(r)]
    origin = existing[0].origin if existing else PointM(x=4.0, y=4.0)
    angle = existing[0].angleDeg if existing else 0.0
    along = existing[0].along if existing else "x"
    out: list[ParkingRowSpec] = []
    if trucks:
        sample = truck_rows[0] if truck_rows else None
        make = ParkingRowSpec(
            id="trucks",
            stalls=trucks,
            stallWidthM=5.0,
            stallLengthM=17.0,
            angleDeg=sample.angleDeg if sample else angle,
            origin=sample.origin if sample else origin,
            along=sample.along if sample else along,
            charger=(
                sample.charger.model_copy(update={"startNo": 1})
                if sample and sample.charger
                else ChargerOnRow(type="none", startNo=1, side="head")
            ),
            labelPrefix=(sample.labelPrefix if sample and sample.labelPrefix else "重卡直流桩"),
        )
        out.extend(_resize_kind_rows(truck_rows, trucks, make=make))
    if cars:
        sample = car_rows[0] if car_rows else None
        car_origin = sample.origin if sample else PointM(x=origin.x, y=origin.y + 20.0)
        make = ParkingRowSpec(
            id="cars",
            stalls=cars,
            stallWidthM=3.0,
            stallLengthM=6.0,
            angleDeg=sample.angleDeg if sample else angle,
            origin=car_origin,
            along=sample.along if sample else along,
            charger=(
                sample.charger.model_copy(update={"startNo": 1})
                if sample and sample.charger
                else ChargerOnRow(type="none", startNo=1, side="head")
            ),
            labelPrefix=(sample.labelPrefix if sample and sample.labelPrefix else "直流充电桩"),
        )
        out.extend(_resize_kind_rows(car_rows, cars, make=make))
    if out:
        plan.parkingRows = out


def _ensure_transformers(
    plan: EvChargingStationPlan, *, count: int, kva: float | None
) -> None:
    feeders = [eq for eq in plan.equipment if eq.type in _FEEDER]
    others = [eq for eq in plan.equipment if eq.type not in _FEEDER]
    if int(count) <= 0:
        plan.equipment = others
        return
    n = max(1, min(int(count), 8))
    cap = kva
    while len(feeders) < n:
        i = len(feeders) + 1
        x = max(6.0, plan.site.widthM - 4.0)
        y = max(6.0, plan.site.heightM - 4.0 - (i - 1) * 14.0)
        feeders.append(
            EquipmentSpec(
                id=f"tx{i}",
                type="box_transformer",
                x=x,
                y=y,
                label=f"{cap:g}kVA箱变" if cap else f"{i}#箱变",
                capacityKva=cap,
            )
        )
    feeders = feeders[:n]
    if cap is not None:
        for eq in feeders:
            eq.capacityKva = cap
            eq.type = "box_transformer"
            eq.label = f"{cap:g}kVA箱变"
    plan.equipment = feeders + others


def is_electrical_room(building: BuildingSpec) -> bool:
    blob = f"{building.label or ''} {building.id or ''}"
    return any(tok in blob for tok in _ROOM_LABELS) or str(building.id or "") == "elec_room"


_TX_ASKED_RE = re.compile(r"箱变|变压器|箱式变|变电")


def prune_unasked_transformers(plan: EvChargingStationPlan, query: str) -> None:
    """用户没点名箱变/配电室时，丢掉模型示例和 seed 里塞进来的箱变。"""
    brief = parse_layout_brief(query)
    if brief.transformer_n is not None or brief.transformer_kva is not None:
        return
    if brief.electrical_room:
        return
    if _TX_ASKED_RE.search(query or ""):
        return
    plan.equipment = [eq for eq in plan.equipment if eq.type not in _FEEDER]
    if plan.legend:
        plan.legend = [
            s for s in plan.legend if str(s) not in ("box_transformer", "ring_box_transformer")
        ]


def apply_equipment_additions(plan: EvChargingStationPlan, brief: LayoutBrief) -> None:
    """按用户本句补配电室、箱变容量/台数、群冲主机柜；坐标由 pack 落到空白地。"""
    if brief.transformer_n is not None or brief.transformer_kva is not None:
        _ensure_transformers(
            plan, count=brief.transformer_n or 1, kva=brief.transformer_kva
        )
    if brief.electrical_room:
        n_tx = brief.transformer_n or sum(1 for e in plan.equipment if e.type in _FEEDER) or 2
        _ensure_electrical_room(plan, tx_n=n_tx)
    if brief.host_n is not None:
        _ensure_group_hosts(plan, count=brief.host_n, kw=brief.host_kw)


def _ensure_electrical_room(plan: EvChargingStationPlan, *, tx_n: int) -> None:
    if any(is_electrical_room(b) for b in plan.buildings):
        return
    n = max(1, min(int(tx_n), 8))
    rw = max(8.0, 3.4 * n + 3.2)
    rh = 6.2
    plan.buildings.append(
        BuildingSpec(
            id="elec_room",
            label="配电室",
            kind="building",
            rect=RectM(x=0.8, y=0.8, w=rw, h=rh),
        )
    )


def _ensure_group_hosts(
    plan: EvChargingStationPlan, *, count: int, kw: float | None
) -> None:
    hosts = [
        eq
        for eq in plan.equipment
        if eq.type == "group_host" or any(tok in (eq.label or "") for tok in ("群冲", "群充"))
    ]
    others = [eq for eq in plan.equipment if eq not in hosts]
    n = max(1, min(int(count), 8))
    label = f"{kw:g}kW群冲主机柜" if kw else "群冲主机柜"
    while len(hosts) < n:
        i = len(hosts) + 1
        hosts.append(
            EquipmentSpec(
                id=f"gh{i}",
                type="group_host",
                x=4.0 + i * 3.2,
                y=4.0,
                label=label,
            )
        )
    hosts = hosts[:n]
    for eq in hosts:
        eq.type = "group_host"
        eq.label = label
        eq.capacityKva = None
    plan.equipment = others + hosts


def _parse_gate_location(blob: str) -> tuple[str | None, str | None]:
    """从用户话/读图取出入口所在边，以及偏东/偏西等角位。"""
    if not blob:
        return None, None
    if any(
        tok in blob
        for tok in ("两处出入口", "两个出入口", "两扇门", "多处出入口", "南北出入口")
    ):
        return None, None
    low = blob.lower()
    corners: list[tuple[tuple[str, ...], str, str]] = [
        (
            (
                "东南角",
                "东南出入口",
                "出入口在东南",
                "东南侧出入口",
                "右下",
                "南墙东",
                "南侧东",
                "南侧偏东",
                "south-east",
                "south_east",
                "southeast",
                "bottom right",
                "bottom-right",
                "lower right",
            ),
            "south",
            "east",
        ),
        (
            (
                "西南角",
                "西南出入口",
                "出入口在西南",
                "左下",
                "南墙西",
                "南侧西",
                "南侧偏西",
                "south-west",
                "south_west",
                "southwest",
                "bottom left",
                "bottom-left",
                "lower left",
            ),
            "south",
            "west",
        ),
        (
            (
                "东北角",
                "东北出入口",
                "出入口在东北",
                "右上",
                "north-east",
                "northeast",
            ),
            "north",
            "east",
        ),
        (
            (
                "西北角",
                "西北出入口",
                "出入口在西北",
                "左上",
                "north-west",
                "northwest",
            ),
            "north",
            "west",
        ),
    ]
    for tokens, side, along in corners:
        if any(tok in blob or tok in low for tok in tokens):
            return side, along
    if "东南" in blob or "southeast" in low:
        return "south", "east"
    if re.search(r"(南侧|南墙|正南).{0,6}(出入口|入口|大门)|(出入口|入口|大门).{0,10}(南侧|南墙|正南)", blob):
        return "south", None
    if re.search(r"(北侧|北墙|正北).{0,6}(出入口|入口|大门)|(出入口|入口|大门).{0,10}(北侧|北墙|正北)", blob):
        return "north", None
    if re.search(r"(东侧|东墙|正东).{0,6}(出入口|入口|大门)|(出入口|入口|大门).{0,10}(东侧|东墙|正东)", blob):
        return "east", None
    if re.search(r"(西侧|西墙|正西).{0,6}(出入口|入口|大门)|(出入口|入口|大门).{0,10}(西侧|西墙|正西)", blob):
        return "west", None
    return None, None


def infer_gate_along(
    *,
    side: str,
    offset: float,
    width: float,
    span: float,
) -> str | None:
    """由已有 offset 还原角位：南墙偏东 → east。居中返回 None。"""
    if span <= 0.5:
        return None
    mid = float(offset) + float(width) / 2.0
    frac = mid / float(span)
    if side in {"north", "south"}:
        if frac >= 0.58:
            return "east"
        if frac <= 0.42:
            return "west"
        return None
    if side in {"east", "west"}:
        if frac >= 0.58:
            return "north"
        if frac <= 0.42:
            return "south"
        return None
    return None


def gate_is_roughly_centered(
    *, offset: float, width: float, span: float, tol: float = 0.08
) -> bool:
    """示例 JSON / 种子门常落在边长正中，这种才允许被「东南角」覆盖。"""
    if span <= 0.5:
        return True
    mid = float(offset) + float(width) / 2.0
    return abs(mid - float(span) / 2.0) <= max(2.0, float(span) * tol)


def apply_gate_from_texts(plan: EvChargingStationPlan, *texts: str) -> EvChargingStationPlan:
    """用户原文或读图点名了角位时，把门从南墙正中挪到该角。"""
    brief = parse_layout_brief(*texts)
    if not brief.gate_side:
        return plan
    if brief.gate_along is None and not brief.gate_east:
        return plan
    out = plan.model_copy(deep=True)
    _apply_gate_hint(
        out, side=brief.gate_side, east=brief.gate_east, along=brief.gate_along
    )
    return out


def gate_offset_on_span(
    along: str | None, *, side: str, span: float, width: float, flush: bool = False
) -> float:
    """along=east 表示南/北墙上靠东端（东南/东北角）。flush=True 时紧贴该端墙。"""
    margin = 0.2 if flush else 2.0
    width = min(float(width), max(0.5, float(span) - 0.5))
    if side in {"north", "south"}:
        if along == "east":
            return max(0.0, span - width - margin)
        if along == "west":
            return margin if span > width + margin else 0.0
    if side in {"east", "west"}:
        if along == "north":
            return max(0.0, span - width - margin)
        if along == "south":
            return margin if span > width + margin else 0.0
    return max(0.0, (span - width) / 2)


def _apply_gate_hint(
    plan: EvChargingStationPlan,
    *,
    side: str,
    east: bool = False,
    along: str | None = None,
) -> None:
    if along is None and east:
        along = "east"
    existing = collect_site_gates(plan.site)
    # 只写了「南侧出入口」且无角位时，不要把已有门位改到居中。
    if existing and along is None:
        return
    w = plan.site.widthM
    h = plan.site.heightM
    span = w if side in {"north", "south"} else h
    # 草稿/上一张已经把门开在角落时，靠墙重排不得改写门位。
    same_side = [g for g in existing if str(g.side) == side]
    if same_side and not all(
        gate_is_roughly_centered(offset=g.offsetM, width=g.widthM, span=span)
        for g in same_side
    ):
        return
    width = min(10.0, max(6.0, span * 0.12))
    offset = gate_offset_on_span(
        along,
        side=side,
        span=span,
        width=width,
        flush=along in {"east", "west", "north", "south"},
    )
    offset, width = clamp_gate_to_span(offset, width, span)
    hinted = GateSpec(
        side=side,  # type: ignore[arg-type]
        offsetM=offset,
        widthM=width,
        label="出入口",
    )
    existing = collect_site_gates(plan.site)
    if len(existing) <= 1:
        set_site_gates(plan.site, [hinted])
        return
    out: list[GateSpec] = []
    replaced = False
    for g in existing:
        if g.side == side and not replaced:
            out.append(hinted)
            replaced = True
        else:
            out.append(g)
    if not replaced:
        out.append(hinted)
    set_site_gates(plan.site, out)
