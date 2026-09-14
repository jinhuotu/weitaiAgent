"""图纸左下设计说明：按当前布置生成，不让 LLM 填。"""

from __future__ import annotations

import re
from typing import Any

from api.services.layouts.rules import CAR_STALL, TRUCK_STALL
from api.services.layouts.schema import EvChargingStationPlan

_KW = {
    "dc_320kw": "320kW",
    "dc_160kw": "160kW",
    "dc_120kw": "120kW",
    "ac_14kw": "14kW",
}
_QUERY_KW = re.compile(r"(\d+(?:\.\d+)?)\s*[kK][wW]")
_QUERY_KVA = re.compile(r"(\d+(?:\.\d+)?)\s*[kK][vV][aA]")


def ensure_cad_notes(
    plan: EvChargingStationPlan,
    *,
    query: str = "",
    constraints: Any = None,
) -> list[str]:
    """保证第一条是站内规模摘要；已有同类说明则不重复。"""
    summary = station_summary_note(plan, query=query, constraints=constraints)
    notes = [str(n).strip() for n in (plan.notes or []) if str(n).strip()]
    if not any(_is_summary_note(n) for n in notes):
        notes.insert(0, summary)
    extra = _survey_note(plan)
    if extra and extra not in notes:
        notes.append(extra)
    plan.notes = notes[:12]
    ensure_cad_legend(plan)
    return plan.notes


def ensure_cad_legend(plan: EvChargingStationPlan) -> list[str]:
    """图例补上方案里已有、出图用得着的符号。"""
    have = [str(s) for s in (plan.legend or [])]
    seen = set(have)
    extra: list[str] = []
    types = {str(eq.type) for eq in plan.equipment}
    mapping = (
        ("fire_hydrant", "fire_hydrant"),
        ("cable_well", "cable_well"),
        ("vent_grille", "vent_grille"),
        ("box_transformer", "box_transformer"),
        ("ring_cabinet", "ring_cabinet"),
        ("group_host", "group_host"),
    )
    for kind, legend in mapping:
        if kind in types and legend not in seen:
            extra.append(legend)
            seen.add(legend)
    if plan.trees and "tree" not in seen:
        extra.append("tree")
        seen.add("tree")
    if plan.greenery and "greenery" not in seen:
        extra.append("greenery")
        seen.add("greenery")
    if extra:
        plan.legend = [*have, *extra]
    return list(plan.legend)


def _survey_note(plan: EvChargingStationPlan) -> str:
    survey = getattr(plan.site, "survey", None)
    if survey is None:
        return ""
    name = str(survey.name or "").strip()
    ox, oy = float(survey.originXm or 0.0), float(survey.originYm or 0.0)
    rot = float(survey.rotationDeg or 0.0)
    if not name and abs(ox) <= 1e-6 and abs(oy) <= 1e-6 and abs(rot) <= 1e-6:
        return ""
    bits = [f"本图采用{name or '测量坐标'}"]
    if abs(ox) > 1e-6 or abs(oy) > 1e-6:
        bits.append(f"场地西南角对应 E={ox:g} N={oy:g}(m)")
    if abs(rot) > 1e-6:
        bits.append(f"局部X轴相对东向旋转{rot:g}°")
    return "2." + "，".join(bits) + "。"


def station_summary_note(
    plan: EvChargingStationPlan,
    *,
    query: str = "",
    constraints: Any = None,
) -> str:
    trucks = sum(int(r.stalls) for r in plan.parkingRows if float(r.stallLengthM) >= 10)
    cars = sum(int(r.stalls) for r in plan.parkingRows if float(r.stallLengthM) < 10)
    piles = 0
    pile_kw = ""
    for row in plan.parkingRows:
        if row.charger is None or row.charger.type in {"none", ""}:
            continue
        piles += int(row.stalls)
        pile_kw = pile_kw or _KW.get(str(row.charger.type), "")
    pile_kw = _query_kw(query) or _constraint_kw(constraints) or pile_kw or "直流"
    txs = [
        eq
        for eq in plan.equipment
        if eq.type in {"box_transformer", "ring_box_transformer", "ring_cabinet"}
    ]
    kvas = [float(eq.capacityKva) for eq in txs if eq.capacityKva]
    kva = _query_kva(query) or (kvas[0] if kvas else None)
    tw, tl = TRUCK_STALL
    cw, cl = CAR_STALL
    bits: list[str] = []
    if trucks:
        bits.append(f"重卡车位{trucks}个，重卡净车位{tw:g}*{tl:g}(m)")
    if cars:
        bits.append(f"轿车车位{cars}个，轿车净车位{cw:g}*{cl:g}(m)")
    if piles:
        kind = "重卡直流桩" if trucks and trucks >= piles else "直流桩"
        bits.append(f"{piles}台{pile_kw}{kind}")
    if txs:
        if kva:
            bits.append(f"{len(txs)}台{kva:g}kVA箱变")
        else:
            bits.append(f"{len(txs)}台箱变")
    if not bits:
        return "1.本图为充电站总平面布置图，尺寸以图面标注为准。"
    return "1.站内含" + "，".join(bits) + "。"


def _is_summary_note(text: str) -> bool:
    raw = text.replace(" ", "")
    return raw.startswith("1.") and ("站内含" in raw or "车位" in raw and "箱变" in raw)


def _query_kw(query: str) -> str:
    found = _QUERY_KW.findall(query or "")
    if not found:
        return ""
    return f"{found[0]}kW"


def _query_kva(query: str) -> float | None:
    found = _QUERY_KVA.findall(query or "")
    if not found:
        return None
    try:
        return float(found[0])
    except (TypeError, ValueError):
        return None


def _constraint_kw(constraints: Any) -> str:
    if constraints is None:
        return ""
    dc = getattr(getattr(constraints, "chargers", None), "dcType", None)
    if isinstance(constraints, dict):
        chargers = constraints.get("chargers") or {}
        dc = chargers.get("dcType") if isinstance(chargers, dict) else dc
    return _KW.get(str(dc or ""), "")
