"""布置修订：识别改参/整改、锁定上一版场地外形，并执行靠墙等空间指令。"""

from __future__ import annotations

import re
from typing import Any, Literal

from api.services.layouts.brief import (
    apply_equipment_additions,
    apply_layout_brief,
    is_electrical_room,
    parse_layout_brief,
)
from api.services.layouts.schema import (
    ChargerOnRow,
    EvChargingStationPlan,
    ParkingRowSpec,
    PointM,
    coerce_charger_type,
)

_REVISE_RE = re.compile(
    r"(改成|改为|修改|换成|调整|全部改|改一下|统一改|整改|挪|移到|放到|靠墙|贴墙|贴边|"
    r"功率|桩型|车位数|可否|能不能|重新排|优化|斜列|垂直|平行|横排|竖排|"
    r"箱变|变压器|配电|东北|东南|西北|西南|间距|拉开|合并|靠近|"
    r"北侧|南侧|东侧|西侧|入口改|出入口改|"
    r"\d+\s*kW|\d+\s*千瓦)",
    re.I,
)
_FRESH_LAYOUT_RE = re.compile(
    r"(帮我画|我想|规划|布置图|有一个.{0,12}场地|足球场|标准.{0,8}场地|"
    r"如图|草稿|红线|新场地|重新布置|重新规划)"
)
_NEW_SITE_RE = re.compile(
    r"(重新布置|重新规划|新场地|换(一张|张)?(图|草稿)|另一(张|个)场地|新的草稿)"
)
_CHARGER_TO_RE = re.compile(
    r"(?:修改为|修改成|改成|改为|换成|换为|调整为|调整成|全部改成|统一改成|统一改为)"
    r"\s*(\d+)\s*(?:kW|千瓦)",
    re.I,
)
_CHARGER_KW_ALL_RE = re.compile(r"(\d+)\s*(?:kW|千瓦)", re.I)
_CHARGER_WORD_RE = re.compile(r"(320\s*kW|160\s*kW|120\s*kW|14\s*kW|交流桩|直流桩)", re.I)
_KNOWN_DC = {"dc_320kw", "dc_160kw", "dc_120kw", "ac_14kw"}
_WALL_RE = re.compile(
    r"靠墙|贴墙|贴边|靠(?:着)?墙|"
    r"靠(?:左侧|右侧|左边|右边|西|东|南|北)(?:侧|边|面|墙)?|"
    r"靠(?:西|东|南|北)墙|"
    r"(?:左侧|右侧|西面|东面|南面|北面|西墙|东墙|南墙|北墙).{0,4}墙?"
)
_SIDE_RE = re.compile(r"(北|南|东|西)(?:侧|边|墙|面)?")
_MIDDLE_RE = re.compile(r"中间|当中|中央|靠中")
_COUNT_IN_HINT_RE = re.compile(r"(\d+)\s*个")
_STALL_RANGE_RE = re.compile(
    r"(\d+)\s*[-~～—到至]\s*(\d+)\s*(?:号)?"
)
_VERTICAL_RE = re.compile(r"竖着|竖向|立着|纵向|顺着墙|竖排")
_ANGLE_RE = re.compile(
    r"(?:斜列|斜向|斜着|倾斜)\s*(-?\d{1,2})\s*度?"
    r"|(-?\d{1,2})\s*度\s*(?:斜列|斜向|斜停车)?"
    r"|(斜列|斜向|斜着|斜停车|垂直(?:停车|布置)?|正交|平行(?:道路|车道)?|横排|水平)",
    re.I,
)
_EQUIP_MOVE_RE = re.compile(
    r"(箱变|变压器|箱式变|环网柜|配电柜|配电室|设备)"
    r".{0,12}(?:挪|移|放|调).{0,10}"
    r"(东北|东南|西北|西南|东侧|西侧|南侧|北侧|东边|西边|南边|北边|"
    r"右上|右下|左上|左下|靠东|靠西|靠南|靠北)"
    r"|"
    r"(?:把|将)?(?:箱变|变压器|箱式变|环网柜).{0,8}"
    r"(东北|东南|西北|西南|东侧|西侧|南侧|北侧)(?:角|侧|边)?",
    re.I,
)
_CORNER_RE = re.compile(
    r"(东北|东南|西北|西南|东侧|西侧|南侧|北侧|东边|西边|南边|北边|"
    r"右上|右下|左上|左下|靠东|靠西|靠南|靠北)"
)
_FEEDER_TYPES = {
    "ring_cabinet",
    "box_transformer",
    "ring_box_transformer",
    "lv_cabinet",
}

WallSide = Literal["north", "south", "east", "west"]
Corner = Literal["ne", "se", "nw", "sw", "east", "west", "north", "south"]


def has_spatial_revise_intent(query: str) -> bool:
    """用户话里是否带靠墙/编号重摆/斜列/挪箱变等可执行空间指令。"""
    q = (query or "").strip()
    if not q:
        return False
    return bool(
        _WALL_RE.search(q)
        or _STALL_RANGE_RE.search(q)
        or _VERTICAL_RE.search(q)
        or _ANGLE_RE.search(q)
        or _EQUIP_MOVE_RE.search(q)
        or any(
            tok in q
            for tok in (
                "贴边",
                "移到",
                "挪到",
                "竖着",
                "竖排",
                "出入口",
                "入口改",
            )
        )
    )


def should_program_revise(state: dict[str, Any]) -> bool:
    """有上一张布置且本轮是改图：应由程序修订，不要让 LLM 整案重画。"""
    prior = state.get("priorLayout")
    if not isinstance(prior, dict) or prior.get("kind") != "ev_charging_station_plan":
        return False
    if state.get("layoutRevise"):
        return True
    q = _as_query(state)
    return has_spatial_revise_intent(q) or parse_charger_type_hint(q) is not None


def _as_query(state: dict[str, Any]) -> str:
    q = state.get("query")
    if isinstance(q, str) and q.strip():
        return q.strip()
    raw = state.get("input")
    if isinstance(raw, dict):
        return str(raw.get("query") or raw.get("text") or "").strip()
    return str(raw or "").strip()


def is_layout_revise_query(
    query: str,
    *,
    has_prior: bool,
    has_images: bool,
) -> bool:
    """有上一张布置，且本轮是改参数/整改而不是换新场地时，走增量修订。"""
    if not has_prior:
        return False
    q = (query or "").strip()
    if not q:
        return False
    if _NEW_SITE_RE.search(q):
        return False
    if has_spatial_revise_intent(q) or _REVISE_RE.search(q) or parse_charger_type_hint(q):
        if _FRESH_LAYOUT_RE.search(q) and not (
            has_spatial_revise_intent(q)
            or any(
                tok in q
                for tok in ("整改", "挪", "移到", "改成", "改为", "优化", "斜列", "靠墙", "修改")
            )
        ):
            return False
        return True
    if _FRESH_LAYOUT_RE.search(q):
        return False
    if has_images and any(tok in q for tok in ("如图", "规划", "草稿")) and not _REVISE_RE.search(
        q
    ):
        return False
    return not has_images and len(q) <= 48


def parse_charger_type_hint(*texts: str) -> str | None:
    """从用户原话取目标桩型。『把160KW改成320KW』必须取 320，不能命中句首的 160。"""
    blob = "\n".join(t for t in texts if t)
    if not blob:
        return None

    def _as_type(num: str) -> str | None:
        raw = str(coerce_charger_type(f"{num}kW"))
        return raw if raw in _KNOWN_DC else None

    named = [_as_type(m.group(1)) for m in _CHARGER_TO_RE.finditer(blob)]
    named = [t for t in named if t]
    if named:
        return named[-1]
    found = [_as_type(n) for n in _CHARGER_KW_ALL_RE.findall(blob)]
    found = [t for t in found if t]
    if found:
        if len(found) >= 2 and re.search(r"改|换|调整", blob):
            return found[-1]
        return found[0]
    m2 = _CHARGER_WORD_RE.search(blob)
    if not m2:
        return None
    raw = m2.group(1).lower().replace(" ", "")
    if "交流" in raw or "14" in raw:
        return "ac_14kw"
    if "320" in raw:
        return "dc_320kw"
    if "160" in raw:
        return "dc_160kw"
    if "120" in raw:
        return "dc_120kw"
    return None


_CHARGER_LABEL = {
    "dc_320kw": "320kW直流充电桩",
    "dc_160kw": "160kW直流充电桩",
    "dc_120kw": "120kW直流充电桩",
    "ac_14kw": "14kW交流充电桩",
}


def _nudge_row_origin(row: ParkingRowSpec, skip: int) -> None:
    """切排后把后段 origin 沿排列方向错开，避免 160kW 段叠画在 320kW 段上。"""
    if skip <= 0:
        return
    pitch = float(row.pitchM or (float(row.stallWidthM) + 0.8))
    if row.along == "y":
        row.origin = row.origin.model_copy(update={"y": row.origin.y + pitch * skip})
    else:
        row.origin = row.origin.model_copy(update={"x": row.origin.x + pitch * skip})


def apply_charger_type(
    plan: EvChargingStationPlan,
    charger_type: str | None,
    stall_range: tuple[int, int] | None = None,
) -> None:
    if not charger_type or charger_type == "none":
        return
    label = _CHARGER_LABEL.get(charger_type)
    if stall_range is None:
        for row in plan.parkingRows:
            if row.charger is None or row.charger.type == "none":
                continue
            if charger_type in {"ac_14kw"} or float(row.stallLengthM) < 10:
                row.charger.type = charger_type  # type: ignore[assignment]
                if label:
                    row.labelPrefix = label
        return
    lo, hi = stall_range
    def _range_hits() -> bool:
        for row in plan.parkingRows:
            start = int(row.charger.startNo) if row.charger else 1
            end = start + max(1, int(row.stalls)) - 1
            if max(start, lo) <= min(end, hi):
                return True
        return False

    # 各排 startNo 仍是默认 1 时，按 27-50 涂色永远碰不到 → 先按当前排顺序编号再涂
    if not _range_hits():
        n = 1
        for row in plan.parkingRows:
            if row.charger:
                row.charger = row.charger.model_copy(update={"startNo": n})
            n += max(1, int(row.stalls))
    new_rows: list[ParkingRowSpec] = []
    for row in plan.parkingRows:
        start = int(row.charger.startNo) if row.charger else 1
        end = start + max(1, int(row.stalls)) - 1
        overlap_lo = max(start, lo)
        overlap_hi = min(end, hi)
        if overlap_lo > overlap_hi:
            new_rows.append(row)
            continue
        if start < overlap_lo:
            head = row.model_copy(deep=True)
            head.id = f"{row.id}_a"
            head.stalls = overlap_lo - start
            if head.charger:
                head.charger = head.charger.model_copy(update={"startNo": start})
            new_rows.append(head)
        mid = row.model_copy(deep=True)
        mid.id = row.id if start >= overlap_lo and end <= overlap_hi else f"{row.id}_m"
        mid.stalls = overlap_hi - overlap_lo + 1
        _nudge_row_origin(mid, overlap_lo - start)
        apply_here = charger_type in {"ac_14kw"} or float(mid.stallLengthM) < 10
        if mid.charger:
            update: dict[str, Any] = {"startNo": overlap_lo}
            if apply_here:
                update["type"] = charger_type
            mid.charger = mid.charger.model_copy(update=update)
        elif apply_here:
            mid.charger = ChargerOnRow(type=charger_type, startNo=overlap_lo, side="head")  # type: ignore[arg-type]
        if apply_here and label:
            mid.labelPrefix = label
        new_rows.append(mid)
        if overlap_hi < end:
            tail = row.model_copy(deep=True)
            tail.id = f"{row.id}_b"
            tail.stalls = end - overlap_hi
            _nudge_row_origin(tail, overlap_hi + 1 - start)
            if tail.charger:
                tail.charger = tail.charger.model_copy(update={"startNo": overlap_hi + 1})
            new_rows.append(tail)
    plan.parkingRows = new_rows


def apply_query_charger_to_plan(plan: EvChargingStationPlan, query: str) -> str | None:
    """把用户本句点名的桩型写回各排，供出图图例/标注使用。"""
    wanted = parse_charger_type_hint(query)
    if not wanted:
        return None
    apply_charger_type(plan, wanted, stall_range=_parse_stall_range(query))
    return wanted


def user_asked_to_move_gate(query: str) -> bool:
    """仅当用户点名改门时才允许动出入口，提及南侧出入口不算改门。"""
    q = (query or "").strip()
    if not q:
        return False
    return bool(
        re.search(
            r"(入口|出入口|大门).{0,12}(改|挪|移)|"
            r"(改|挪|移).{0,8}(入口|出入口|大门)|"
            r"入口改|出入口改|"
            r"(出入口|入口|大门)在.{0,6}(东南|东北|西南|西北|东侧|西侧|南侧|北侧|东边|西边)",
            q,
        )
    )


def should_relayout_equipment(query: str, brief: Any | None = None) -> bool:
    """增加配电室/箱变/群冲或明确挪设备时，不要沿用上一张箱变坐标。"""
    q = query or ""
    b = brief or parse_layout_brief(q)
    if _EQUIP_MOVE_RE.search(q):
        return True
    if getattr(b, "electrical_room", False) or getattr(b, "host_n", None) is not None:
        return True
    if getattr(b, "transformer_n", None) is not None or getattr(b, "transformer_kva", None) is not None:
        return True
    return any(tok in q for tok in ("配电室", "群冲", "群充", "主机柜"))


def is_equipment_only_revise(query: str, brief: Any | None = None) -> bool:
    """只加设备、不改车位数：保留已有车位排，不要重新 fit。"""
    b = brief or parse_layout_brief(query)
    if b.cars is not None or b.trucks is not None:
        return False
    return should_relayout_equipment(query, b)


def lock_site_geometry(
    plan: EvChargingStationPlan,
    prior: EvChargingStationPlan,
    *,
    keep_gate: bool = False,
) -> EvChargingStationPlan:
    """改参时外框、建筑、道路跟上一张；用户点名改门时保留新出入口。"""
    out = plan.model_copy(deep=True)
    out.site.widthM = prior.site.widthM
    out.site.heightM = prior.site.heightM
    out.site.northDeg = prior.site.northDeg
    out.site.polygon = [PointM(x=p.x, y=p.y) for p in (prior.site.polygon or [])]
    if not keep_gate and (prior.site.gate is not None or prior.site.gates):
        out.site.gate = prior.site.gate
        out.site.gates = list(prior.site.gates or [])
    out.buildings = list(prior.buildings)
    added_rooms = [b for b in plan.buildings if is_electrical_room(b)]
    if added_rooms and not any(is_electrical_room(b) for b in out.buildings):
        out.buildings.extend(added_rooms)
    out.roads = list(prior.roads)
    out.greenery = list(prior.greenery)
    return out


def preserve_parking_layout(
    plan: EvChargingStationPlan, prior: EvChargingStationPlan
) -> EvChargingStationPlan:
    """数量未变时，强制沿用上一版各排 origin/角度/沿轴，避免 LLM 暗中整场重排。"""
    out = plan.model_copy(deep=True)
    if not prior.parkingRows:
        return out
    prior_n = sum(int(r.stalls) for r in prior.parkingRows)
    cur_n = sum(int(r.stalls) for r in out.parkingRows)
    if prior_n != cur_n or not out.parkingRows:
        return out
    if len(out.parkingRows) == len(prior.parkingRows):
        for cur, old in zip(out.parkingRows, prior.parkingRows):
            cur.origin = PointM(x=old.origin.x, y=old.origin.y)
            cur.angleDeg = old.angleDeg
            cur.along = old.along
            cur.id = old.id
        return out
    # 排数变了但总车位数相同：按上一版几何均摊数量，仍保原点序列
    out.parkingRows = [r.model_copy(deep=True) for r in prior.parkingRows]
    return out


def _row_center_y(row: ParkingRowSpec) -> float:
    return float(row.origin.y) + float(row.stallLengthM) * 0.5


def _row_center_x(row: ParkingRowSpec) -> float:
    return float(row.origin.x) + float(row.stallWidthM) * 0.5


def _parse_stall_range(query: str) -> tuple[int, int] | None:
    """识别『1-8号』『1~8』『车位1-8』；不要把 160kW 这类功率数字当成桩号。"""
    q = query or ""
    for m in _STALL_RANGE_RE.finditer(q):
        a, b = int(m.group(1)), int(m.group(2))
        lo, hi = (a, b) if a <= b else (b, a)
        if lo < 1 or hi > 80 or hi - lo > 40:
            continue
        after = q[m.end() : m.end() + 8]
        if re.match(r"\s*(?:kW|千瓦|kva|米|m\b)", after, flags=re.I):
            continue
        return lo, hi
    return None


def _convert_stall_range_to_trucks(
    plan: EvChargingStationPlan, *, lo: int, hi: int, truck_n: int
) -> EvChargingStationPlan:
    """把编号 lo..hi 的车位改成 truck_n 个重卡车位，其余排保留。"""
    out = plan.model_copy(deep=True)
    units: list[dict[str, Any]] = []
    for row in out.parkingRows:
        start = int(row.charger.startNo) if row.charger else 1
        for i in range(int(row.stalls)):
            units.append({"no": start + i, "row": row, "row_id": row.id})
    units.sort(key=lambda u: u["no"])
    target = [u for u in units if lo <= int(u["no"]) <= hi]
    rest = [u for u in units if not (lo <= int(u["no"]) <= hi)]
    if not target:
        n = hi - lo + 1
        target, rest = units[:n], units[n:]
    if not target:
        return out
    sample = target[0]["row"]
    side = sample.charger.side if sample.charger else "head"
    truck_n = max(1, min(80, int(truck_n)))
    moved = ParkingRowSpec(
        id=f"trucks_{lo}_{hi}",
        stalls=truck_n,
        stallWidthM=5.0,
        stallLengthM=17.0,
        angleDeg=0.0,
        origin=sample.origin,
        along=sample.along,
        charger=(
            sample.charger.model_copy(update={"startNo": lo})
            if sample.charger
            else ChargerOnRow(type="none", startNo=lo, side=side)
        ),
        labelPrefix="重卡直流桩",
    )
    other_rows: list[ParkingRowSpec] = []
    next_no = lo + truck_n
    if rest:
        by_id: dict[str, list[dict[str, Any]]] = {}
        for u in rest:
            by_id.setdefault(str(u["row_id"]), []).append(u)
        for row_id, group in by_id.items():
            r0 = group[0]["row"].model_copy(deep=True)
            r0.id = row_id if row_id != moved.id else f"rest_{row_id}"
            r0.stalls = len(group)
            if r0.charger:
                r0.charger = r0.charger.model_copy(update={"startNo": next_no})
            else:
                r0.charger = ChargerOnRow(type="none", startNo=next_no, side="head")
            next_no += r0.stalls
            other_rows.append(r0)
    out.parkingRows = [moved, *other_rows]
    return out


def _pick_rows_for_hint(plan: EvChargingStationPlan, query: str) -> list[ParkingRowSpec]:
    rows = list(plan.parkingRows)
    if not rows:
        return []
    want_n = None
    m = _COUNT_IN_HINT_RE.search(query)
    if m and any(
        tok in query for tok in ("中间", "靠墙", "车位", "桩", "北侧", "南侧", "东侧", "西侧")
    ):
        want_n = int(m.group(1))
    if _MIDDLE_RE.search(query):
        cy = float(plan.site.heightM) * 0.5
        ranked = sorted(rows, key=lambda r: abs(_row_center_y(r) - cy))
        if want_n:
            picked: list[ParkingRowSpec] = []
            got = 0
            for r in ranked:
                picked.append(r)
                got += int(r.stalls)
                if got >= want_n:
                    break
            return picked or ranked[:1]
        n = max(1, (len(ranked) + 1) // 2)
        return ranked[:n]
    # 按方位挑排：北侧/南侧/东侧/西侧
    side_pick: WallSide | None = None
    if "北侧" in query or "北边" in query or "北面" in query:
        side_pick = "north"
    elif "南侧" in query or "南边" in query or "南面" in query:
        side_pick = "south"
    elif "东侧" in query or "东边" in query or "东面" in query:
        side_pick = "east"
    elif "西侧" in query or "西边" in query or "西面" in query:
        side_pick = "west"
    if side_pick == "north":
        ranked = sorted(rows, key=lambda r: -_row_center_y(r))
    elif side_pick == "south":
        ranked = sorted(rows, key=lambda r: _row_center_y(r))
    elif side_pick == "east":
        ranked = sorted(rows, key=lambda r: -_row_center_x(r))
    elif side_pick == "west":
        ranked = sorted(rows, key=lambda r: _row_center_x(r))
    else:
        ranked = []
    if ranked:
        if want_n:
            picked = []
            got = 0
            for r in ranked:
                picked.append(r)
                got += int(r.stalls)
                if got >= want_n:
                    break
            return picked or ranked[:1]
        return ranked[:1]
    if want_n:
        exact = [r for r in rows if int(r.stalls) == want_n]
        if exact:
            return exact
    return rows


def _infer_wall_side(plan: EvChargingStationPlan, query: str) -> WallSide:
    del plan
    q = query or ""
    # 左/右必须优先：口语「左侧墙」以前会落到默认北墙
    if any(tok in q for tok in ("左侧", "左边", "左墙", "左面")):
        return "west"
    if any(tok in q for tok in ("右侧", "右边", "右墙", "右面")):
        return "east"
    if any(tok in q for tok in ("上侧", "上边", "上墙")):
        return "north"
    if any(tok in q for tok in ("下侧", "下边", "下墙")):
        return "south"
    m = _SIDE_RE.search(q)
    if m:
        return {"北": "north", "南": "south", "东": "east", "西": "west"}[m.group(1)]  # type: ignore[return-value]
    return "north"


def _parse_angle_hint(query: str) -> float | None:
    q = (query or "").strip()
    if not q:
        return None
    m = _ANGLE_RE.search(q)
    if not m:
        return None
    if m.group(1):
        return float(m.group(1))
    if m.group(2):
        return float(m.group(2))
    word = (m.group(3) or "").strip()
    if word.startswith("斜"):
        return -45.0
    if word.startswith("垂直") or word == "正交":
        return 90.0
    if word.startswith("平行") or word in {"横排", "水平"}:
        return 0.0
    return None


def _apply_angle_hints(plan: EvChargingStationPlan, query: str) -> EvChargingStationPlan:
    angle = _parse_angle_hint(query)
    if angle is None or not plan.parkingRows:
        return plan
    out = plan.model_copy(deep=True)
    # 有靠墙/编号时只改目标排；否则改全部车位排
    if (
        _WALL_RE.search(query)
        or _STALL_RANGE_RE.search(query)
        or _MIDDLE_RE.search(query)
        or any(tok in query for tok in ("北侧", "南侧", "东侧", "西侧"))
    ):
        targets = {r.id for r in _pick_rows_for_hint(out, query)}
    else:
        targets = {r.id for r in out.parkingRows}
    for row in out.parkingRows:
        if row.id not in targets:
            continue
        if abs(angle) >= 80:
            row.angleDeg = 0.0
            row.along = "y" if row.along == "x" else row.along
            if "垂直" in query or "竖" in query:
                row.along = "y"
        else:
            row.angleDeg = angle
            if abs(angle) < 1e-6:
                row.along = "x"
    return out


def _infer_corner(query: str) -> Corner | None:
    m = _CORNER_RE.search(query or "")
    if not m:
        return None
    tok = m.group(1)
    mapping: dict[str, Corner] = {
        "东北": "ne",
        "东南": "se",
        "西北": "nw",
        "西南": "sw",
        "东侧": "east",
        "东边": "east",
        "靠东": "east",
        "西侧": "west",
        "西边": "west",
        "靠西": "west",
        "南侧": "south",
        "南边": "south",
        "靠南": "south",
        "北侧": "north",
        "北边": "north",
        "靠北": "north",
        "右上": "ne",
        "右下": "se",
        "左上": "nw",
        "左下": "sw",
    }
    return mapping.get(tok)


def _corner_xy(plan: EvChargingStationPlan, corner: Corner, *, margin: float = 3.0) -> tuple[float, float]:
    w, h = float(plan.site.widthM), float(plan.site.heightM)
    if corner == "ne":
        return w - margin, h - margin
    if corner == "se":
        return w - margin, margin
    if corner == "nw":
        return margin, h - margin
    if corner == "sw":
        return margin, margin
    if corner == "east":
        return w - margin, h * 0.5
    if corner == "west":
        return margin, h * 0.5
    if corner == "north":
        return w * 0.5, h - margin
    return w * 0.5, margin


def _apply_equipment_move(plan: EvChargingStationPlan, query: str) -> EvChargingStationPlan:
    if not _EQUIP_MOVE_RE.search(query or ""):
        return plan
    corner = _infer_corner(query)
    if corner is None:
        return plan
    out = plan.model_copy(deep=True)
    feeders = [eq for eq in out.equipment if eq.type in _FEEDER_TYPES]
    if not feeders:
        return out
    x0, y0 = _corner_xy(out, corner)
    # 多台箱变沿短边错开，避免叠在同一点
    step = 4.0
    for i, eq in enumerate(feeders):
        if corner in {"ne", "se", "east"}:
            eq.x = max(2.0, x0 - i * step)
            eq.y = y0
        elif corner in {"nw", "sw", "west"}:
            eq.x = min(float(out.site.widthM) - 2.0, x0 + i * step)
            eq.y = y0
        elif corner == "north":
            eq.x = x0 + (i - (len(feeders) - 1) / 2) * step
            eq.y = y0
        else:
            eq.x = x0 + (i - (len(feeders) - 1) / 2) * step
            eq.y = y0
    return out


def _pin_row_to_wall(
    row: ParkingRowSpec, plan: EvChargingStationPlan, side: WallSide, *, margin: float = 0.6
) -> None:
    from api.services.layouts.pack import _row_aabb, _shift_row

    w, h = float(plan.site.widthM), float(plan.site.heightM)
    north_limit = h - margin
    if side == "north" and plan.buildings:
        for b in plan.buildings:
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


def _orient_row_along_wall(
    row: ParkingRowSpec, side: WallSide, *, vertical: bool
) -> None:
    from api.services.layouts.pack import charger_side_for_wall

    if vertical or side in {"west", "east"}:
        row.along = "y"
        row.angleDeg = 0.0
    else:
        row.along = "x"
        row.angleDeg = 0.0
    # 贴墙：桩在墙侧，开口朝场内，避免挡车辆进出
    wall_side = charger_side_for_wall(side)
    if row.charger is None:
        row.charger = ChargerOnRow(type="none", startNo=1, side=wall_side)  # type: ignore[arg-type]
    else:
        row.charger = row.charger.model_copy(update={"side": wall_side})


def _place_row_at_wall(
    row: ParkingRowSpec,
    plan: EvChargingStationPlan,
    side: WallSide,
    *,
    margin: float = 0.8,
) -> None:
    from api.services.layouts.pack import row_pitch

    w, h = float(plan.site.widthM), float(plan.site.heightM)
    pitch = row_pitch(row)
    span = pitch * max(0, int(row.stalls) - 1) + (
        float(row.stallLengthM) if row.along == "y" else float(row.stallWidthM)
    )
    if side == "west":
        row.origin = PointM(x=margin, y=max(margin, (h - span) * 0.5))
    elif side == "east":
        row.origin = PointM(
            x=max(margin, w - float(row.stallWidthM) - margin),
            y=max(margin, (h - span) * 0.5),
        )
    elif side == "north":
        row.origin = PointM(
            x=max(margin, (w - span) * 0.5),
            y=max(margin, h - float(row.stallLengthM) - margin),
        )
    else:
        row.origin = PointM(x=max(margin, (w - span) * 0.5), y=margin)
    _pin_row_to_wall(row, plan, side, margin=margin)


def _relayout_stall_numbers(
    plan: EvChargingStationPlan,
    *,
    lo: int,
    hi: int,
    side: WallSide,
    vertical: bool,
) -> EvChargingStationPlan:
    """把编号 lo..hi 抽成一排，按靠墙+竖/横重摆；其余排尽量保留原位。"""
    out = plan.model_copy(deep=True)
    units: list[dict[str, Any]] = []
    for row in out.parkingRows:
        start = int(row.charger.startNo) if row.charger else 1
        for i in range(int(row.stalls)):
            units.append({"no": start + i, "row": row, "row_id": row.id})
    units.sort(key=lambda u: u["no"])
    target = [u for u in units if lo <= int(u["no"]) <= hi]
    rest = [u for u in units if not (lo <= int(u["no"]) <= hi)]
    if not target:
        n = hi - lo + 1
        target, rest = units[:n], units[n:]
    sample = target[0]["row"]
    moved = sample.model_copy(deep=True)
    moved.id = f"wall_{side}_{lo}_{hi}"
    moved.stalls = len(target)
    if moved.charger:
        moved.charger = moved.charger.model_copy(update={"startNo": lo})
    else:
        moved.charger = ChargerOnRow(type="none", startNo=lo, side="head")
    _orient_row_along_wall(moved, side, vertical=vertical)
    _place_row_at_wall(moved, out, side)

    # 其余车位按原排 id 聚合，保留各排原点，避免整场重装
    other_rows: list[ParkingRowSpec] = []
    if rest:
        by_id: dict[str, list[dict[str, Any]]] = {}
        for u in rest:
            by_id.setdefault(str(u["row_id"]), []).append(u)
        next_no = hi + 1
        for row_id, group in by_id.items():
            r0 = group[0]["row"].model_copy(deep=True)
            r0.id = row_id if row_id not in {moved.id} else f"rest_{row_id}"
            r0.stalls = len(group)
            if r0.charger:
                r0.charger = r0.charger.model_copy(update={"startNo": next_no})
            else:
                r0.charger = ChargerOnRow(type="none", startNo=next_no, side="head")
            next_no += r0.stalls
            other_rows.append(r0)

    out.parkingRows = [moved, *other_rows]
    return out


def apply_spatial_hints(plan: EvChargingStationPlan, query: str) -> EvChargingStationPlan:
    """执行靠墙/竖排/斜列/箱变挪位/按编号整改。"""
    q = (query or "").strip()
    if not q:
        return plan
    out = plan
    out = _apply_equipment_move(out, q)
    out = _apply_angle_hints(out, q)
    if not out.parkingRows:
        return out
    place = bool(
        _WALL_RE.search(q)
        or "贴边" in q
        or "移到" in q
        or "挪到" in q
        or _VERTICAL_RE.search(q)
    )
    # 仅点名 1-8 号改桩型/改成重卡时，不要当成靠墙重摆
    if not place:
        return out
    side = _infer_wall_side(out, q)
    vertical = bool(_VERTICAL_RE.search(q)) or side in {"west", "east"}
    rng = _parse_stall_range(q)
    if rng is not None:
        return _relayout_stall_numbers(
            out, lo=rng[0], hi=rng[1], side=side, vertical=vertical
        )
    out = out.model_copy(deep=True)
    targets = _pick_rows_for_hint(out, q)
    target_ids = {r.id for r in targets}
    for row in out.parkingRows:
        if row.id not in target_ids:
            continue
        _orient_row_along_wall(row, side, vertical=vertical)
        _place_row_at_wall(row, out, side)
    return out


def revise_plan_from_prior(
    prior: EvChargingStationPlan,
    *,
    query: str,
    llm_plan: EvChargingStationPlan | None = None,
) -> EvChargingStationPlan:
    """以上一张为底，套用户改参与空间整改；锁场地外形，默认不整场重排。"""
    brief = parse_layout_brief(query)
    base = prior.model_copy(deep=True)
    rng = _parse_stall_range(query)
    converted_range = False
    if rng is not None and brief.trucks is not None:
        out = _convert_stall_range_to_trucks(
            base, lo=rng[0], hi=rng[1], truck_n=int(brief.trucks)
        )
        converted_range = True
    else:
        out = apply_layout_brief(base, query)
    out = lock_site_geometry(out, prior, keep_gate=user_asked_to_move_gate(query))
    # 用户没说车位数时，禁止用模型输出的 stalls 覆盖上一张（否则 4+4 会被改成 6+6）
    if not converted_range:
        out = preserve_parking_layout(out, prior)
    charger = parse_charger_type_hint(query)
    if charger is None and llm_plan is not None and rng is None:
        for row in llm_plan.parkingRows:
            if row.charger and row.charger.type != "none":
                charger = row.charger.type
                break
    if charger and not converted_range:
        apply_charger_type(out, charger, stall_range=rng)
    apply_equipment_additions(out, brief)
    # 箱变位置：用户明确说挪、或新增配电室/群冲/箱变时重布；否则保留上一版坐标
    if not should_relayout_equipment(query, brief):
        prior_feed = [eq for eq in prior.equipment if eq.type in _FEEDER_TYPES]
        cur_feed = [eq for eq in out.equipment if eq.type in _FEEDER_TYPES]
        others = [eq for eq in out.equipment if eq.type not in _FEEDER_TYPES]
        if prior_feed and cur_feed:
            merged = []
            for i, eq in enumerate(cur_feed):
                src = prior_feed[min(i, len(prior_feed) - 1)]
                merged.append(
                    eq.model_copy(
                        update={
                            "x": src.x,
                            "y": src.y,
                            "id": eq.id or src.id,
                        }
                    )
                )
            out.equipment = merged + others
    out = apply_spatial_hints(out, query)
    return out


def describe_plan_changes(
    prior: EvChargingStationPlan | None,
    plan: EvChargingStationPlan,
    *,
    query: str = "",
) -> list[str]:
    """出图后给用户看的改参说明：改了什么、什么没动。"""
    if prior is None:
        return []
    lines: list[str] = []

    def _car_dc(p: EvChargingStationPlan) -> str | None:
        for row in p.parkingRows:
            if (
                row.charger
                and row.charger.type in {"dc_320kw", "dc_160kw", "dc_120kw", "ac_14kw"}
                and float(row.stallLengthM) < 10
            ):
                return str(row.charger.type)
        return None

    rng = _parse_stall_range(query)
    old_dc, new_dc = _car_dc(prior), _car_dc(plan)
    wanted = parse_charger_type_hint(query)
    if rng is not None and wanted:
        lo, hi = rng
        matched = 0
        for row in plan.parkingRows:
            if not row.charger or row.charger.type != wanted:
                continue
            start = int(row.charger.startNo)
            end = start + int(row.stalls) - 1
            matched += max(0, min(end, hi) - max(start, lo) + 1)
        if matched >= hi - lo + 1:
            lines.append(
                f"已将 {lo}–{hi} 号充电桩改为{_CHARGER_LABEL.get(wanted, wanted)}。"
            )
        elif matched:
            lines.append(
                f"已将 {matched} 台充电桩改为{_CHARGER_LABEL.get(wanted, wanted)}。"
            )
    elif old_dc and new_dc and old_dc != new_dc:
        lines.append(
            f"已修改：充电桩由{_CHARGER_LABEL.get(old_dc, old_dc)}"
            f"改为{_CHARGER_LABEL.get(new_dc, new_dc)}。"
        )
    elif wanted and new_dc == wanted and old_dc == wanted:
        lines.append(f"已核对：轿车充电桩保持为{_CHARGER_LABEL.get(new_dc, new_dc)}。")
    user_brief = parse_layout_brief(query)
    if rng is not None and user_brief.trucks is not None:
        lines.append(
            f"已将 {rng[0]}–{rng[1]} 号车位改为 {user_brief.trucks} 个重卡车位。"
        )

    old_n = sum(int(r.stalls) for r in prior.parkingRows)
    new_n = sum(int(r.stalls) for r in plan.parkingRows)
    if old_n != new_n:
        lines.append(f"已调整：车位由 {old_n} 个改为 {new_n} 个。")

    def _tx(p: EvChargingStationPlan) -> tuple[int, list[float]]:
        eqs = [eq for eq in p.equipment if eq.type in _FEEDER_TYPES]
        kvas = [float(eq.capacityKva or 0) for eq in eqs]
        return len(eqs), kvas

    old_tn, old_kva = _tx(prior)
    new_tn, new_kva = _tx(plan)
    if old_tn != new_tn:
        lines.append(f"已调整：箱变由 {old_tn} 台改为 {new_tn} 台。")
    elif old_kva and new_kva and old_kva[0] != new_kva[0] and old_kva[0] and new_kva[0]:
        lines.append(f"已修改：箱变容量由 {old_kva[0]:g}kVA 改为 {new_kva[0]:g}kVA。")
    if any(is_electrical_room(b) for b in plan.buildings) and not any(
        is_electrical_room(b) for b in prior.buildings
    ):
        lines.append("已增加：配电室，箱变布置在室内。")
    old_h = sum(1 for e in prior.equipment if e.type == "group_host")
    new_h = sum(1 for e in plan.equipment if e.type == "group_host")
    if new_h != old_h:
        lines.append(f"已增加：群冲主机柜 {new_h} 台。")

    from api.services.layouts.schema import collect_site_gates

    old_gates = [(str(g.side), round(float(g.offsetM), 1)) for g in collect_site_gates(prior.site)]
    new_gates = [(str(g.side), round(float(g.offsetM), 1)) for g in collect_site_gates(plan.site)]
    if old_gates != new_gates:
        sides = "、".join(s for s, _ in new_gates) or "无"
        lines.append(f"已调整：出入口改为 {sides}。")

    old_ang = [float(r.angleDeg) for r in prior.parkingRows if float(r.stallLengthM) < 10]
    new_ang = [float(r.angleDeg) for r in plan.parkingRows if float(r.stallLengthM) < 10]
    if old_ang and new_ang and abs(old_ang[0] - new_ang[0]) > 4:
        lines.append(f"已调整：斜列角由 {old_ang[0]:g}° 改为 {new_ang[0]:g}°。")

    site_same = (
        abs(float(prior.site.widthM) - float(plan.site.widthM)) < 0.05
        and abs(float(prior.site.heightM) - float(plan.site.heightM)) < 0.05
        and old_n == new_n
    )
    if site_same:
        lines.append("场地尺寸与车位数量未改。")
    elif old_n == new_n and (
        float(plan.site.widthM) > float(prior.site.widthM) + 0.05
        or float(plan.site.heightM) > float(prior.site.heightM) + 0.05
    ):
        lines.append(
            f"已放大场地至 {plan.site.widthM:g}m×{plan.site.heightM:g}m，全部车位落在红线内。"
        )
    if not lines:
        lines.append("已按上一张布置重新出图。")
    return lines[:6]


def compact_plan_for_prompt(plan: dict[str, Any] | EvChargingStationPlan) -> str:
    import json

    if isinstance(plan, EvChargingStationPlan):
        data = plan.model_dump(mode="json")
    else:
        data = dict(plan)
    data["trenches"] = []
    data["cables"] = []
    data["trees"] = data.get("trees") or []
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))
