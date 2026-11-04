"""v2 强制条件表：本单字段由 DeepSeek 抽取，目录尺寸仍以 rules 为准。"""

from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from api.services.layouts.brief import LayoutBrief
from api.services.layouts.check import CheckIssue, check_plan, lock_catalog_sizes
from api.services.layouts.parse import extract_json_object
from api.services.layouts.schema import (
    EvChargingStationPlan,
    coerce_charger_type,
    coerce_gate_side,
    collect_site_gates,
)

CONSTRAINTS_KIND = "ev_charging_station_constraints"
_FEEDER = {
    "ring_cabinet",
    "box_transformer",
    "ring_box_transformer",
    "lv_cabinet",
}
_WALL_PHRASE = {
    "north": "靠北墙布置",
    "south": "靠南墙布置",
    "east": "靠东墙布置",
    "west": "靠西墙布置",
}


class ConstraintModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class ConstraintGate(ConstraintModel):
    side: Literal["north", "south", "east", "west"] = "south"
    widthM: float | None = Field(default=None, gt=0, le=80)
    along: Literal["east", "west", "north", "south", "center"] | None = None
    label: str = "出入口"

    @field_validator("side", mode="before")
    @classmethod
    def _side(cls, v: Any) -> Any:
        return coerce_gate_side(v) or "south"

    @field_validator("along", mode="before")
    @classmethod
    def _along(cls, v: Any) -> Any:
        if v in (None, "", "null", "none"):
            return None
        raw = str(v).strip().lower()
        aliases = {
            "east": "east",
            "west": "west",
            "north": "north",
            "south": "south",
            "center": "center",
            "东": "east",
            "西": "west",
            "北": "north",
            "南": "south",
            "中": "center",
            "居中": "center",
            "southeast": "east",
            "southwest": "west",
            "northeast": "east",
            "northwest": "west",
        }
        return aliases.get(raw, aliases.get(str(v).strip(), None))


class ConstraintSite(ConstraintModel):
    areaM2: float | None = Field(default=None, ge=20, le=200_000)
    widthM: float | None = Field(default=None, gt=0, le=500)
    heightM: float | None = Field(default=None, gt=0, le=500)
    shapeFrom: Literal["vision", "user_rect", "unknown"] = "unknown"
    gates: list[ConstraintGate] = Field(default_factory=list, max_length=8)

    @field_validator("shapeFrom", mode="before")
    @classmethod
    def _shape(cls, v: Any) -> Any:
        raw = str(v or "").strip().lower()
        if raw in {"vision", "user_rect", "unknown"}:
            return raw
        return "unknown"


class ConstraintFleet(ConstraintModel):
    cars: int | None = Field(default=None, ge=0, le=80)
    trucks: int | None = Field(default=None, ge=0, le=80)
    piles: int | None = Field(default=None, ge=0, le=80)
    pileEqualsStall: bool = True


class ConstraintChargers(ConstraintModel):
    dcType: Literal["dc_320kw", "dc_160kw", "dc_120kw"] | None = None
    acCount: int = Field(default=0, ge=0, le=80)
    acType: Literal["ac_14kw"] = "ac_14kw"

    @field_validator("dcType", mode="before")
    @classmethod
    def _dc(cls, v: Any) -> Any:
        if v in (None, "", "null", "none"):
            return None
        raw = coerce_charger_type(v)
        if raw in {"dc_320kw", "dc_160kw", "dc_120kw"}:
            return raw
        return None


class ConstraintTransformer(ConstraintModel):
    count: int = Field(default=1, ge=1, le=8)
    kva: float | None = Field(default=None, ge=0, le=20000)
    type: str = "box_transformer"


class ConstraintLayout(ConstraintModel):
    mode: Literal["parallel", "single_row_angle", "dual_row_angle"] | None = None
    angleDeg: float | None = Field(default=None, ge=-90, le=90)
    wallSide: Literal["north", "south", "east", "west"] | None = None
    backToBack: bool | None = None

    @field_validator("mode", mode="before")
    @classmethod
    def _mode(cls, v: Any) -> Any:
        if v in (None, "", "null"):
            return None
        raw = str(v).strip().lower()
        aliases = {
            "parallel": "parallel",
            "平行": "parallel",
            "single_row_angle": "single_row_angle",
            "单排斜列": "single_row_angle",
            "dual_row_angle": "dual_row_angle",
            "双排斜列": "dual_row_angle",
        }
        return aliases.get(raw, aliases.get(str(v).strip(), None))

    @field_validator("wallSide", mode="before")
    @classmethod
    def _wall(cls, v: Any) -> Any:
        if v in (None, "", "null"):
            return None
        return coerce_gate_side(v)


class LayoutConstraints(ConstraintModel):
    schemaVersion: Literal["1"] = "1"
    kind: Literal["ev_charging_station_constraints"] = CONSTRAINTS_KIND
    source: str = "user_turn"
    site: ConstraintSite = Field(default_factory=ConstraintSite)
    fleet: ConstraintFleet = Field(default_factory=ConstraintFleet)
    chargers: ConstraintChargers = Field(default_factory=ConstraintChargers)
    transformers: list[ConstraintTransformer] = Field(default_factory=list, max_length=8)
    layout: ConstraintLayout = Field(default_factory=ConstraintLayout)
    priority: list[str] = Field(
        default_factory=lambda: [
            "user_text",
            "catalog",
            "vision_geometry",
            "case_mode_only",
        ]
    )
    forbid: list[str] = Field(
        default_factory=lambda: [
            "copy_case_counts",
            "copy_case_kva",
            "copy_case_site",
            "use_ocr_digits_as_spec",
            "drop_stalls_if_tight",
        ]
    )
    unset: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list, max_length=12)


def parse_constraints_text(text: str) -> LayoutConstraints:
    obj = extract_json_object(text)
    return LayoutConstraints.model_validate(obj)


def parse_constraints_any(value: Any) -> LayoutConstraints | None:
    if value in (None, "", False, {}, []):
        return None
    if isinstance(value, LayoutConstraints):
        return value
    if isinstance(value, dict):
        kind = str(value.get("kind") or "")
        if kind and kind != CONSTRAINTS_KIND:
            return None
        return LayoutConstraints.model_validate(value)
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            parsed = parse_constraints_text(raw)
        except Exception:  # noqa: BLE001
            return None
        if parsed.kind != CONSTRAINTS_KIND:
            return None
        return parsed
    return None


def constraints_from_user_text(*texts: str) -> LayoutConstraints | None:
    """抽条件 LLM 空返回时，从用户原话补一张条件表，避免出图直接失败。"""
    from api.services.layouts.brief import parse_layout_brief
    from api.services.layouts.revise import parse_charger_type_hint

    brief = parse_layout_brief(*texts)
    dc = parse_charger_type_hint(*texts)
    if not brief.has_scale() and dc is None:
        return None
    gates = []
    if brief.gate_side:
        along = brief.gate_along if brief.gate_along in {"east", "west", "north", "south", "center"} else (
            "east" if brief.gate_east else None
        )
        gates.append(
            ConstraintGate(side=brief.gate_side, along=along, label="出入口")  # type: ignore[arg-type]
        )
    transformers: list[ConstraintTransformer] = []
    if brief.transformer_n:
        transformers.append(
            ConstraintTransformer(count=int(brief.transformer_n), kva=brief.transformer_kva)
        )
    cars = brief.cars
    trucks = brief.trucks
    piles = None
    if cars is not None or trucks is not None:
        piles = int(cars or 0) + int(trucks or 0)
    shape: Literal["vision", "user_rect", "unknown"] = (
        "user_rect" if brief.site_w and brief.site_h else "unknown"
    )
    chargers = ConstraintChargers()
    if dc in {"dc_320kw", "dc_160kw", "dc_120kw"}:
        chargers = ConstraintChargers(dcType=dc)  # type: ignore[arg-type]
    return LayoutConstraints(
        source="user_text_fallback",
        site=ConstraintSite(
            widthM=brief.site_w,
            heightM=brief.site_h,
            areaM2=brief.site_area_m2,
            shapeFrom=shape,
            gates=gates,
        ),
        fleet=ConstraintFleet(cars=cars, trucks=trucks, piles=piles),
        chargers=chargers,
        transformers=transformers,
    )


def overlay_user_text_on_constraints(
    cons: LayoutConstraints,
    *texts: str,
    overwrite_counts: bool = False,
) -> LayoutConstraints:
    """条件 JSON 解析成功但桩数/面积/箱变是空的时，用原话补上，避免种子默认 8 台。"""
    from api.services.layouts.brief import parse_layout_brief
    from api.services.layouts.revise import parse_charger_type_hint

    blob = "\n".join(t for t in texts if t).strip()
    if not blob:
        return cons
    brief = parse_layout_brief(*texts)
    dc = parse_charger_type_hint(*texts)
    out = cons.model_copy(deep=True)
    empty_fleet = (
        out.fleet.cars is None
        and out.fleet.trucks is None
        and (out.fleet.piles is None or int(out.fleet.piles) <= 0)
    )
    # 模型常抄示例里的 8 台；用户原话是别的数时盖掉。已填的 12/4 重卡不要动。
    copied_eight = (
        overwrite_counts
        and out.fleet.cars == 8
        and (out.fleet.trucks in (None, 0))
        and brief.cars is not None
        and brief.cars != 8
    )
    if (empty_fleet or copied_eight) and (
        brief.cars is not None or brief.trucks is not None
    ):
        cars = brief.cars if brief.cars is not None else 0
        trucks = brief.trucks if brief.trucks is not None else 0
        out.fleet.cars = cars
        out.fleet.trucks = trucks
        out.fleet.piles = int(cars) + int(trucks)
    if brief.site_area_m2 is not None and out.site.areaM2 is None:
        out.site.areaM2 = float(brief.site_area_m2)
    if brief.site_w and brief.site_h and not (out.site.widthM and out.site.heightM):
        out.site.widthM = float(brief.site_w)
        out.site.heightM = float(brief.site_h)
        out.site.shapeFrom = "user_rect"
    if brief.transformer_n and not out.transformers:
        out.transformers = [
            ConstraintTransformer(
                count=int(brief.transformer_n), kva=brief.transformer_kva
            )
        ]
    if dc in {"dc_320kw", "dc_160kw", "dc_120kw"} and out.chargers.dcType is None:
        out.chargers.dcType = dc  # type: ignore[assignment]
    return out


def _along_from_plan_gate(plan: EvChargingStationPlan) -> str | None:
    from api.services.layouts.brief import infer_gate_along

    gates = collect_site_gates(plan.site)
    if not gates:
        return None
    g = gates[0]
    span = float(plan.site.widthM if g.side in {"north", "south"} else plan.site.heightM)
    return infer_gate_along(
        side=str(g.side), offset=float(g.offsetM), width=float(g.widthM), span=span
    )


def merge_gate_along_from_context(
    cons: LayoutConstraints,
    *,
    query: str,
    vision: str,
    prior: EvChargingStationPlan | None = None,
) -> LayoutConstraints:
    """抽条件只写了 side=south 时，用读图/上一张的角位补 along，避免种子门居中。"""
    from api.services.layouts.brief import parse_layout_brief

    out = cons.model_copy(deep=True)
    brief = parse_layout_brief(query, vision)
    if brief.site_w and brief.site_h:
        out.site.widthM = float(brief.site_w)
        out.site.heightM = float(brief.site_h)
        out.site.shapeFrom = "user_rect"
    if brief.site_area_m2 is not None and out.site.areaM2 is None:
        out.site.areaM2 = float(brief.site_area_m2)
    along = brief.gate_along or ("east" if brief.gate_east else None)
    if along is None and prior is not None:
        along = _along_from_plan_gate(prior)
    if brief.gate_side:
        out.site.gates = [
            ConstraintGate(
                side=brief.gate_side,  # type: ignore[arg-type]
                along=along,  # type: ignore[arg-type]
                widthM=out.site.gates[0].widthM if out.site.gates else None,
                label="出入口",
            )
        ]
        return out
    if along is None:
        return out
    if out.site.gates:
        for gate in out.site.gates:
            if gate.along is None:
                gate.along = along  # type: ignore[assignment]
        return out
    side = brief.gate_side or (str(collect_site_gates(prior.site)[0].side) if prior else "south")
    if prior is not None and not brief.gate_side:
        gates = collect_site_gates(prior.site)
        if gates:
            side = str(gates[0].side)
    out.site.gates = [
        ConstraintGate(side=side, along=along, label="出入口")  # type: ignore[arg-type]
    ]
    return out


def wanted_stalls(cons: LayoutConstraints) -> tuple[int | None, int | None]:
    cars = cons.fleet.cars
    trucks = cons.fleet.trucks
    piles = cons.fleet.piles
    if cars is None and trucks is None and piles is not None:
        return piles, 0
    return cars, trucks


def transformer_need(cons: LayoutConstraints) -> tuple[int | None, float | None]:
    if not cons.transformers:
        return None, None
    count = sum(max(1, int(item.count)) for item in cons.transformers)
    kvas = [float(item.kva) for item in cons.transformers if item.kva is not None]
    return count, (kvas[0] if kvas else None)


def seed_plan_payload(cons: LayoutConstraints, *, query: str = "") -> dict[str, Any]:
    """条件表可出图时的最小布置 JSON，供模型 JSON 损坏时兜底。"""
    from api.services.layouts.brief import parse_layout_brief

    cars, trucks = wanted_stalls(cons)
    cars_n = int(cars or 0)
    trucks_n = int(trucks or 0)
    brief = parse_layout_brief(query) if query else None
    if cars_n + trucks_n <= 0 and brief is not None:
        cars_n = int(brief.cars or 0)
        trucks_n = int(brief.trucks or 0)
    width = float(cons.site.widthM or 0)
    height = float(cons.site.heightM or 0)
    if width <= 0 or height <= 0:
        along = 1
        if cars_n:
            left = (cars_n + 1) // 2
            along = max(left, cars_n - left, 1)
        car_span = (3.0 + 0.8) * max(0, along - 1) + 3.0 if cars_n else 0.0
        truck_span = (5.0 + 0.8) * max(0, trucks_n - 1) + 5.0 if trucks_n else 0.0
        height = max(16.0, car_span + 1.6, truck_span + 1.6)
        width = max(16.0, 2 * 6.0 + 1.6)
        if trucks_n:
            width = max(width, 17.0 + 1.6)
        area = float(cons.site.areaM2 or 0)
        if area >= 20:
            height = max(height, (area / 1.5) ** 0.5)
            width = max(width, area / max(height, 1.0))
        height = float(math.ceil(height - 1e-9))
        width = float(math.ceil(width - 1e-9))
    gate_side = cons.site.gates[0].side if cons.site.gates else "south"
    gate_w = float(cons.site.gates[0].widthM or 8) if cons.site.gates else 8.0
    gate_along = cons.site.gates[0].along if cons.site.gates else None
    from api.services.layouts.brief import gate_offset_on_span

    span = width if gate_side in {"north", "south"} else height
    gate_off = gate_offset_on_span(
        gate_along,
        side=gate_side,
        span=span,
        width=gate_w,
        flush=bool(gate_along and gate_along != "center"),
    )
    angle = float(cons.layout.angleDeg or 0)
    dc = cons.chargers.dcType if cons.chargers.dcType in {"dc_320kw", "dc_160kw", "dc_120kw"} else None
    if dc is None and query:
        from api.services.layouts.revise import parse_charger_type_hint

        hinted = parse_charger_type_hint(query)
        if hinted in {"dc_320kw", "dc_160kw", "dc_120kw"}:
            dc = hinted
    charger_type = dc or "none"
    tx_n, tx_kva = transformer_need(cons)
    if (tx_n or 0) <= 0 and brief is not None and brief.transformer_n:
        tx_n = int(brief.transformer_n)
        tx_kva = brief.transformer_kva
    tx_n = max(0, int(tx_n or 0))
    kva = float(tx_kva or 2000)
    if kva <= 20:
        kva *= 1000
    rows: list[dict[str, Any]] = []
    if cars_n:
        left = (cars_n + 1) // 2
        right = cars_n - left
        rows.append(
            {
                "id": "cars_w",
                "stalls": left,
                "stallWidthM": 3,
                "stallLengthM": 6,
                "angleDeg": angle,
                "origin": {"x": 1.0, "y": 4.0},
                "along": "y",
                "charger": {"type": charger_type, "startNo": 1, "side": "left"},
                "labelPrefix": "直流充电桩",
            }
        )
        if right:
            rows.append(
                {
                    "id": "cars_e",
                    "stalls": right,
                    "stallWidthM": 3,
                    "stallLengthM": 6,
                    "angleDeg": angle,
                    "origin": {"x": max(8.0, width - 7.0), "y": 4.0},
                    "along": "y",
                "charger": {"type": charger_type, "startNo": left + 1, "side": "right"},
                "labelPrefix": "直流充电桩",
                }
            )
    if trucks_n:
        rows.append(
            {
                "id": "trucks",
                "stalls": trucks_n,
                "stallWidthM": 5,
                "stallLengthM": 17,
                "angleDeg": 0,
                "origin": {"x": 4.0, "y": max(2.0, height - 18.0)},
                "along": "x",
                "charger": {"type": charger_type, "startNo": 1, "side": "head"},
                "labelPrefix": "重卡直流桩",
            }
        )
    equipment = []
    for i in range(tx_n):
        equipment.append(
            {
                "id": f"tx{i + 1}",
                "type": "box_transformer",
                "x": 3.0 + i * 6.0,
                "y": max(4.0, height - 4.0),
                "label": f"{kva:g}kVA箱变",
                "capacityKva": kva,
            }
        )
    title = (query or "").strip()[:40]
    return {
        "schemaVersion": "1",
        "kind": "ev_charging_station_plan",
        "titleBlock": {
            "title": "充电站平面布置图",
            "sheetNo": "001",
            "project": title,
        },
        "site": {
            "widthM": width,
            "heightM": height,
            "northDeg": 0,
            "gate": {
                "side": gate_side,
                "offsetM": gate_off,
                "widthM": gate_w,
                "label": "出入口",
            },
            "polygon": [],
        },
        "buildings": [],
        "parkingRows": rows,
        "equipment": equipment,
        "trenches": [],
        "cables": [],
        "trees": [],
        "greenery": [],
        "roads": [],
        "legend": (
            (["box_transformer"] if tx_n else []) + ([dc] if dc else []) + ["parking"]
        ),
        "notes": [],
    }


def constraints_to_brief(cons: LayoutConstraints) -> LayoutBrief:
    cars, trucks = wanted_stalls(cons)
    tx_n, tx_kva = transformer_need(cons)
    gate_side = cons.site.gates[0].side if cons.site.gates else None
    along = cons.site.gates[0].along if cons.site.gates else None
    return LayoutBrief(
        trucks=trucks,
        cars=cars,
        transformer_n=tx_n,
        transformer_kva=tx_kva,
        site_w=cons.site.widthM,
        site_h=cons.site.heightM,
        site_area_m2=cons.site.areaM2,
        gate_side=gate_side,
        gate_east=along == "east",
        gate_along=along,
    )


def apply_constraint_hints(
    plan: EvChargingStationPlan, cons: LayoutConstraints, query: str = ""
) -> EvChargingStationPlan:
    """条件表能确定的角度/桩型/靠墙；尺寸与数量走 repair_plan(brief)。"""
    out = lock_catalog_sizes(plan.model_copy(deep=True))
    if cons.site.widthM and cons.site.heightM:
        out.site.widthM = float(cons.site.widthM)
        out.site.heightM = float(cons.site.heightM)
        if len(out.site.polygon or []) < 3:
            out.site.polygon = []
    if cons.layout.angleDeg is not None:
        angle = float(cons.layout.angleDeg)
        for row in out.parkingRows:
            if float(row.stallLengthM) < 10:
                row.angleDeg = angle
    if cons.chargers.dcType in {"dc_320kw", "dc_160kw", "dc_120kw"}:
        from api.services.layouts.revise import _parse_stall_range, apply_charger_type

        # 点名 1-8 号改功率时，不要用条件表把两侧都改成同一桩型
        if _parse_stall_range(query) is None:
            apply_charger_type(out, cons.chargers.dcType)
    wall = cons.layout.wallSide
    # 多排时不要整场贴同一面墙，否则车位叠在一起、桩对不齐。
    if wall and len(out.parkingRows) <= 1:
        from api.services.layouts.revise import apply_spatial_hints

        out = apply_spatial_hints(out, _WALL_PHRASE[wall])
    if cons.site.gates:
        g0 = cons.site.gates[0]
        if g0.along in {"east", "west", "north", "south"}:
            from api.services.layouts.brief import _apply_gate_hint

            _apply_gate_hint(out, side=str(g0.side), along=g0.along)
    return out


def check_constraints(
    plan: EvChargingStationPlan, cons: LayoutConstraints
) -> list[CheckIssue]:
    brief = constraints_to_brief(cons)
    issues = list(check_plan(plan, brief))
    if cons.layout.angleDeg is not None:
        want = float(cons.layout.angleDeg)
        for row in plan.parkingRows:
            if float(row.stallLengthM) >= 10:
                continue
            if abs(float(row.angleDeg) - want) > 5:
                issues.append(
                    CheckIssue(
                        "angle",
                        f"斜列角 {row.angleDeg:g}°，用户要求 {want:g}°",
                    )
                )
                break
    dc = cons.chargers.dcType
    if dc in {"dc_320kw", "dc_160kw", "dc_120kw"}:
        for row in plan.parkingRows:
            if row.charger is None or row.charger.type in {"none", "ac_14kw"}:
                continue
            if float(row.stallLengthM) < 10 and row.charger.type != dc:
                issues.append(
                    CheckIssue(
                        "charger_type",
                        f"桩型 {row.charger.type}，用户要求 {dc}",
                        blocking=False,
                    )
                )
                break
    want_cars, want_trucks = wanted_stalls(cons)
    if cons.fleet.piles is not None and cons.fleet.pileEqualsStall:
        got = sum(r.stalls for r in plan.parkingRows)
        if got != int(cons.fleet.piles):
            issues.append(
                CheckIssue(
                    "pile_count",
                    f"车位/桩 {got} 个，用户要求 {cons.fleet.piles} 个",
                )
            )
    elif want_cars is not None or want_trucks is not None:
        got = sum(r.stalls for r in plan.parkingRows)
        need = int(want_cars or 0) + int(want_trucks or 0)
        if need and got != need:
            issues.append(
                CheckIssue("stall_total", f"车位 {got} 个，用户要求 {need} 个")
            )
    if cons.site.gates:
        want_sides = {g.side for g in cons.site.gates}
        got = collect_site_gates(plan.site)
        got_sides = {g.side for g in got}
        span_w = float(plan.site.widthM)
        for cg in cons.site.gates:
            if cg.side != "east":
                continue
            south = next((g for g in got if str(g.side) == "south"), None)
            if south is None:
                continue
            mid = float(south.offsetM) + float(south.widthM) / 2.0
            if cg.along in {"east", None} and mid >= span_w * 0.55:
                want_sides.discard("east")
                want_sides.add("south")
        if want_sides and not want_sides.issubset(got_sides):
            issues.append(
                CheckIssue(
                    "gates",
                    f"出入口 {sorted(got_sides)}，用户要求 {sorted(want_sides)}",
                )
            )
    return issues


def bind_constraints_to_prior(
    cons: LayoutConstraints,
    prior: EvChargingStationPlan,
    *,
    query: str,
) -> LayoutConstraints:
    """用户没点名的规模/场地，一律沿用上一张，禁止抽取节点用案例改写。"""
    from api.services.layouts.brief import parse_layout_brief
    from api.services.layouts.revise import _parse_stall_range, parse_charger_type_hint

    brief = parse_layout_brief(query)
    out = cons.model_copy(deep=True)
    if brief.site_w is None and brief.site_h is None:
        out.site.widthM = float(prior.site.widthM)
        out.site.heightM = float(prior.site.heightM)
        out.site.areaM2 = None
    cars = sum(int(r.stalls) for r in prior.parkingRows if float(r.stallLengthM) < 10)
    trucks = sum(int(r.stalls) for r in prior.parkingRows if float(r.stallLengthM) >= 10)
    rng = _parse_stall_range(query)
    if rng is not None and (brief.cars is not None or brief.trucks is not None):
        lo, hi = rng
        range_cars = range_trucks = 0
        for row in prior.parkingRows:
            start = int(row.charger.startNo) if row.charger else 1
            is_truck = float(row.stallLengthM) >= 10
            for i in range(int(row.stalls)):
                no = start + i
                if lo <= no <= hi:
                    if is_truck:
                        range_trucks += 1
                    else:
                        range_cars += 1
        if brief.trucks is not None:
            out.fleet.trucks = max(0, trucks - range_trucks + int(brief.trucks))
            out.fleet.cars = max(0, cars - range_cars)
        else:
            out.fleet.cars = max(0, cars - range_cars + int(brief.cars or 0))
            out.fleet.trucks = max(0, trucks - range_trucks)
        out.fleet.piles = int(out.fleet.cars or 0) + int(out.fleet.trucks or 0)
    elif brief.cars is None and brief.trucks is None:
        out.fleet.cars = cars
        out.fleet.trucks = trucks
        out.fleet.piles = cars + trucks
    else:
        replace_all = bool(
            any(tok in (query or "") for tok in ("全部", "整场", "只留", "只要"))
            or ("改成" in (query or "") and rng is None and brief.cars is None)
        )
        out.fleet.cars = (
            0
            if (brief.trucks is not None and brief.cars is None and replace_all)
            else (brief.cars if brief.cars is not None else cars)
        )
        out.fleet.trucks = brief.trucks if brief.trucks is not None else trucks
        out.fleet.piles = int(out.fleet.cars or 0) + int(out.fleet.trucks or 0)
    if brief.transformer_n is None:
        n_tx = sum(1 for eq in prior.equipment if eq.type in _FEEDER)
        kvas = [
            float(eq.capacityKva)
            for eq in prior.equipment
            if eq.type in _FEEDER and eq.capacityKva
        ]
        if n_tx:
            out.transformers = [
                ConstraintTransformer(
                    count=n_tx, kva=kvas[0] if kvas else None, type="box_transformer"
                )
            ]
    if brief.gate_side:
        along = brief.gate_along or ("east" if brief.gate_east else None)
        out.site.gates = [
            ConstraintGate(
                side=brief.gate_side,  # type: ignore[arg-type]
                along=along,  # type: ignore[arg-type]
                label="出入口",
            )
        ]
    elif not out.site.gates:
        out.site.gates = []
        for g in collect_site_gates(prior.site):
            span = float(
                prior.site.widthM if g.side in {"north", "south"} else prior.site.heightM
            )
            from api.services.layouts.brief import infer_gate_along

            along = infer_gate_along(
                side=str(g.side),
                offset=float(g.offsetM),
                width=float(g.widthM),
                span=span,
            )
            out.site.gates.append(
                ConstraintGate(
                    side=str(g.side),  # type: ignore[arg-type]
                    widthM=float(g.widthM),
                    along=along,  # type: ignore[arg-type]
                    label=g.label or "出入口",
                )
            )
    else:
        prior_gates = collect_site_gates(prior.site)
        if prior_gates:
            from api.services.layouts.brief import infer_gate_along

            for i, cg in enumerate(out.site.gates):
                if cg.along is not None:
                    continue
                src = prior_gates[min(i, len(prior_gates) - 1)]
                span = float(
                    prior.site.widthM
                    if src.side in {"north", "south"}
                    else prior.site.heightM
                )
                cg.along = infer_gate_along(  # type: ignore[assignment]
                    side=str(src.side),
                    offset=float(src.offsetM),
                    width=float(src.widthM),
                    span=span,
                )
    asked_wall = bool(
        any(tok in (query or "") for tok in ("靠墙", "贴墙", "贴边", "靠北", "靠南", "靠东", "靠西"))
    )
    if out.layout.wallSide and not asked_wall:
        out.layout.wallSide = None
    asked_dc = parse_charger_type_hint(query)
    if asked_dc in {"dc_320kw", "dc_160kw", "dc_120kw"} and rng is None:
        out.chargers.dcType = asked_dc  # type: ignore[assignment]
    elif rng is not None and asked_dc:
        out.chargers.dcType = None
    else:
        prior_dc = next(
            (
                r.charger.type
                for r in prior.parkingRows
                if r.charger and r.charger.type in {"dc_320kw", "dc_160kw", "dc_120kw"}
            ),
            None,
        )
        if prior_dc:
            out.chargers.dcType = prior_dc  # type: ignore[assignment]
    return out
