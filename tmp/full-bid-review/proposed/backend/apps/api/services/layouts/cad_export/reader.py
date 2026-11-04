"""把本程序写出的 DXF 读回方案 JSON。优先藏在图里的真源，再用块位置覆盖设计师改过的点。"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any

from api.services.layouts.cad_export.layers import (
    BLOCK_HYDRANT,
    BLOCK_TRANSFORMER,
    BLOCK_TREE,
    BLOCK_VENT,
    BLOCK_WELL,
    LAYER_SITE,
)
from api.services.layouts.cad_export.meta import extract_plan_json
from api.services.layouts.cad_export.units import MM_PER_M, survey_to_local_m
from api.services.layouts.parse import parse_plan
from api.services.layouts.schema import (
    EquipmentSpec,
    EvChargingStationPlan,
    PointM,
    SurveyFrame,
    TreeSpec,
)

_POINT_BLOCKS = {
    BLOCK_HYDRANT: "fire_hydrant",
    BLOCK_WELL: "cable_well",
    BLOCK_VENT: "vent_grille",
}


def plan_from_dxf(path: str | Path) -> EvChargingStationPlan:
    import ezdxf

    src = Path(path).expanduser().resolve()
    if not src.is_file():
        raise FileNotFoundError(str(src))
    return plan_from_dxf_doc(ezdxf.readfile(str(src)))


def plan_from_dxf_bytes(raw: bytes) -> EvChargingStationPlan:
    import ezdxf

    return plan_from_dxf_doc(ezdxf.read(BytesIO(raw)))


def plan_from_dxf_doc(doc: Any) -> EvChargingStationPlan:
    dumped = extract_plan_json(doc)
    if not dumped:
        raise ValueError("DXF 里没有伟泰方案数据，无法回读")
    plan = parse_plan(dumped)
    _overlay_inserts(plan, doc)
    _overlay_site(plan, doc)
    return plan


def _overlay_inserts(plan: EvChargingStationPlan, doc: Any) -> None:
    survey = plan.site.survey
    by_name: dict[str, list[tuple[float, float]]] = {}
    for entity in doc.modelspace().query("INSERT"):
        name = str(entity.dxf.name or "")
        xy = _insert_local_m(entity, survey)
        if xy is None:
            continue
        by_name.setdefault(name, []).append(xy)

    feeders = [
        eq
        for eq in plan.equipment
        if eq.type in {"box_transformer", "ring_box_transformer", "ring_cabinet"}
    ]
    _assign_nearest(feeders, by_name.get(BLOCK_TRANSFORMER) or [])

    for block, kind in _POINT_BLOCKS.items():
        pts = by_name.get(block) or []
        if not pts:
            continue
        existing = [eq for eq in plan.equipment if eq.type == kind]
        if existing:
            _assign_nearest(existing, pts)
            continue
        for i, (x, y) in enumerate(pts, start=1):
            plan.equipment.append(
                EquipmentSpec(id=f"{kind}_{i}", type=kind, x=x, y=y)  # type: ignore[arg-type]
            )

    trees = by_name.get(BLOCK_TREE) or []
    if trees:
        plan.trees = [TreeSpec(x=x, y=y) for x, y in trees]


def _overlay_site(plan: EvChargingStationPlan, doc: Any) -> None:
    survey = plan.site.survey
    polys: list[list[tuple[float, float]]] = []
    for entity in doc.modelspace().query("LWPOLYLINE"):
        if str(entity.dxf.layer or "") != LAYER_SITE:
            continue
        pts = [(float(p[0]) / MM_PER_M, float(p[1]) / MM_PER_M) for p in entity]
        if len(pts) < 3:
            continue
        local = [survey_to_local_m(survey, x, y) for x, y in pts]
        if local[0] == local[-1] and len(local) > 3:
            local = local[:-1]
        polys.append(local)
    if not polys:
        return
    poly = max(polys, key=len)
    plan.site.polygon = [PointM(x=x, y=y) for x, y in poly]


def _insert_local_m(entity: Any, survey: SurveyFrame | None) -> tuple[float, float] | None:
    try:
        ins = entity.dxf.insert
        sx, sy = float(ins.x) / MM_PER_M, float(ins.y) / MM_PER_M
    except Exception:  # noqa: BLE001
        return None
    return survey_to_local_m(survey, sx, sy)


def _assign_nearest(items: list[Any], pts: list[tuple[float, float]]) -> None:
    if not items or not pts:
        return
    used: set[int] = set()
    for item in items:
        idx = _nearest_index(float(item.x), float(item.y), pts, used)
        if idx is None:
            continue
        used.add(idx)
        item.x, item.y = pts[idx]


def _nearest_index(
    x: float, y: float, pts: list[tuple[float, float]], used: set[int]
) -> int | None:
    best: int | None = None
    best_d = 1e18
    for i, (px, py) in enumerate(pts):
        if i in used:
            continue
        d = (px - x) ** 2 + (py - y) ** 2
        if d < best_d:
            best_d = d
            best = i
    return best
