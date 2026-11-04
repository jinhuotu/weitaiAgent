"""组装 DXF：毫米模型空间几何 + 图纸空间图框。"""

from __future__ import annotations

import math
from io import StringIO
from typing import Any

from ezdxf.enums import TextEntityAlignment

from api.services.layouts.cad_export.blocks import setup_blocks
from api.services.layouts.cad_export.dimensions import DimSpec, build_sheet_dimensions
from api.services.layouts.cad_export.layers import (
    BLOCK_AC_PILE,
    BLOCK_ARROW,
    BLOCK_BUILDING,
    BLOCK_CAR,
    BLOCK_DC_PILE,
    BLOCK_HYDRANT,
    BLOCK_LV,
    BLOCK_METER,
    BLOCK_RING,
    BLOCK_TRANSFORMER,
    BLOCK_TREE,
    BLOCK_TRUCK,
    BLOCK_VENT,
    BLOCK_WELL,
    DIMSTYLE,
    LAYER_ANNO,
    LAYER_CABLE,
    LAYER_DIM,
    LAYER_EQUIP,
    LAYER_GREEN,
    LAYER_PARK,
    LAYER_ROAD,
    LAYER_SITE,
    LAYER_VEHICLE,
    LAYOUT_NAME,
    STYLE_CN,
    setup_dimstyle,
    setup_layers,
    setup_text_style,
)
from api.services.layouts.cad_export.meta import embed_plan_json
from api.services.layouts.cad_export.notes import ensure_cad_notes
from api.services.layouts.cad_export.sheet import draw_sheet, paper_size
from api.services.layouts.cad_export.titleblock import insert_title_block
from api.services.layouts.cad_export.units import (
    INSUNITS_MM,
    choose_paper_and_scale,
    local_to_survey_m,
    mm,
    mm_pt,
    mm_pts,
    survey_is_named,
    survey_needs_transform,
)
from api.services.layouts.pack import (
    _CHARGER_LABELS,
    charger_outset_m,
    charger_point,
    expand_stalls,
    plan_gates,
    rect_corners,
)
from api.services.layouts.schema import (
    EvChargingStationPlan,
    GateSpec,
    PointM,
    RectM,
    site_boundary_m,
    site_is_irregular,
)
from api.services.layouts.symbols import (
    _vehicle_frame,
    aisle_marking_polylines,
    dash_segments,
)


def plan_to_dxf_doc(plan: EvChargingStationPlan, *, query: str = "") -> Any:
    import ezdxf
    from ezdxf import units as ez_units

    ensure_cad_notes(plan, query=query)
    extents = _model_extents_m(plan)
    paper, scale_n = choose_paper_and_scale(
        plan.sheetStyle.paper, plan.sheetStyle.scale, extents
    )
    dim_txt = 2.5 * scale_n
    anno = 2.2 * scale_n

    doc = ezdxf.new("R2013", setup=True)
    doc.units = ez_units.MM
    doc.header["$INSUNITS"] = INSUNITS_MM
    doc.header["$MEASUREMENT"] = 1
    doc.header["$DIMSTYLE"] = DIMSTYLE
    setup_layers(doc)
    setup_text_style(doc)
    setup_dimstyle(doc, text_mm=dim_txt)
    setup_blocks(doc)

    msp = doc.modelspace()
    _draw_site(msp, plan, anno)
    _draw_roads(msp, plan, anno)
    _draw_greenery(msp, plan, anno)
    _draw_aisles(msp, plan, anno)
    _draw_buildings(msp, plan, anno)
    _draw_parking(msp, plan, anno)
    _draw_equipment(msp, plan, anno)
    _draw_trenches(msp, plan, anno)
    _draw_trees(msp, plan)
    if plan.sheetStyle.showDimensions:
        _draw_dimensions(msp, plan)
    _draw_survey_mark(msp, plan, anno)
    embed_plan_json(doc, plan)
    _apply_survey_transform(msp, plan)
    vp_extents = _viewport_extents_m(plan, extents)
    _setup_paperspace(doc, plan, vp_extents, scale_n, paper)
    return doc


def plan_to_dxf_bytes(plan: EvChargingStationPlan, *, query: str = "") -> bytes:
    doc = plan_to_dxf_doc(plan, query=query)
    buf = StringIO()
    doc.write(buf)
    return buf.getvalue().encode("utf-8")


def _setup_paperspace(
    doc: Any,
    plan: EvChargingStationPlan,
    extents: tuple[float, float, float, float],
    scale_n: int,
    paper: str,
) -> None:
    names = list(doc.layouts.names())
    if LAYOUT_NAME in names:
        psp = doc.layouts.get(LAYOUT_NAME)
    elif "Layout1" in names:
        doc.layouts.rename("Layout1", LAYOUT_NAME)
        psp = doc.layouts.get(LAYOUT_NAME)
    else:
        psp = doc.layouts.new(LAYOUT_NAME)
    pw, ph = paper_size(plan, paper)
    psp.page_setup(
        size=(pw, ph),
        margins=(10, 10, 10, 10),
        units="mm",
        scale=(1, scale_n),
        rotation=0,
    )
    used_title = insert_title_block(doc, psp, plan, paper=paper, scale_n=scale_n)
    vp_x0, vp_y0, vp_x1, vp_y1 = draw_sheet(
        psp, plan, scale_n=scale_n, paper=paper, used_title_block=used_title
    )
    min_x, min_y, max_x, max_y = extents
    pad = 4.0
    min_x -= pad
    min_y -= pad
    max_x += pad
    max_y += pad
    ms_cx = mm((min_x + max_x) / 2.0)
    ms_cy = mm((min_y + max_y) / 2.0)
    vp_w = max(40.0, vp_x1 - vp_x0)
    vp_h = max(40.0, vp_y1 - vp_y0)
    psp.add_viewport(
        center=((vp_x0 + vp_x1) / 2.0, (vp_y0 + vp_y1) / 2.0),
        size=(vp_w, vp_h),
        view_center_point=(ms_cx, ms_cy),
        view_height=vp_h * scale_n,
        status=2,
    )
    doc.layouts.set_active_layout(LAYOUT_NAME)
    doc.header["$TILEMODE"] = 0


def _model_extents_m(plan: EvChargingStationPlan) -> tuple[float, float, float, float]:
    """视口范围：场地红线。场外道路只作标注，不把比例撑大。"""
    xs: list[float] = [0.0, float(plan.site.widthM)]
    ys: list[float] = [0.0, float(plan.site.heightM)]
    for x, y in site_boundary_m(plan.site):
        xs.append(x)
        ys.append(y)
    return min(xs), min(ys), max(xs), max(ys)


def _viewport_extents_m(
    plan: EvChargingStationPlan, local: tuple[float, float, float, float]
) -> tuple[float, float, float, float]:
    survey = plan.site.survey
    if not survey_needs_transform(survey):
        return local
    xs: list[float] = []
    ys: list[float] = []
    min_x, min_y, max_x, max_y = local
    for x, y in (
        (min_x, min_y),
        (max_x, min_y),
        (max_x, max_y),
        (min_x, max_y),
    ):
        sx, sy = local_to_survey_m(survey, x, y)
        xs.append(sx)
        ys.append(sy)
    return min(xs), min(ys), max(xs), max(ys)


def _apply_survey_transform(msp: Any, plan: EvChargingStationPlan) -> None:
    survey = plan.site.survey
    if not survey_needs_transform(survey):
        return
    from ezdxf.math import Matrix44

    rad = math.radians(float(survey.rotationDeg or 0.0))
    ox, oy = mm(float(survey.originXm or 0.0)), mm(float(survey.originYm or 0.0))
    matrix = Matrix44.chain(Matrix44.z_rotate(rad), Matrix44.translate(ox, oy, 0.0))
    for entity in list(msp):
        try:
            entity.transform(matrix)
        except Exception:  # noqa: BLE001
            continue


def _draw_survey_mark(msp: Any, plan: EvChargingStationPlan, anno: float) -> None:
    survey = plan.site.survey
    if survey is None or not (
        survey_needs_transform(survey) or survey_is_named(survey)
    ):
        return
    name = (survey.name or "测量坐标").strip()
    ox, oy = float(survey.originXm or 0.0), float(survey.originYm or 0.0)
    label = f"{name} E={ox:g} N={oy:g}"
    _text(msp, label, mm_pt(2.0, -3.2), anno * 0.9, layer=LAYER_ANNO, align="LEFT")


def _draw_site(msp: Any, plan: EvChargingStationPlan, anno: float) -> None:
    w, h = float(plan.site.widthM), float(plan.site.heightM)
    gates = plan_gates(plan)
    if site_is_irregular(plan.site):
        poly = site_boundary_m(plan.site)
        if len(poly) >= 3:
            msp.add_lwpolyline(mm_pts(poly), close=True, dxfattribs={"layer": LAYER_SITE})
    else:
        for a, b in _boundary_segments(w, h, gates):
            if math.hypot(b[0] - a[0], b[1] - a[1]) < 0.08:
                continue
            msp.add_line(mm_pt(*a), mm_pt(*b), dxfattribs={"layer": LAYER_SITE})
    for gate in gates:
        g0, g1 = _gate_ends(gate, w, h)
        inward = _gate_inward(gate.side)
        heading = math.degrees(math.atan2(inward[1], inward[0])) - 90.0
        for end in (g0, g1):
            inner = (end[0] + inward[0] * 1.4, end[1] + inward[1] * 1.4)
            msp.add_line(mm_pt(*end), mm_pt(*inner), dxfattribs={"layer": LAYER_SITE})
        mid = ((g0[0] + g1[0]) / 2.0, (g0[1] + g1[1]) / 2.0)
        outside = (mid[0] - inward[0] * 3.2, mid[1] - inward[1] * 3.2)
        arrow_at = (mid[0] + inward[0] * 2.4, mid[1] + inward[1] * 2.4)
        msp.add_blockref(
            BLOCK_ARROW,
            insert=mm_pt(*arrow_at),
            dxfattribs={
                "layer": LAYER_ROAD,
                "rotation": heading,
                "xscale": 2.4,
                "yscale": 2.4,
            },
        )
        _text(msp, gate.label or "出入口", mm_pt(*outside), anno, layer=LAYER_ROAD)
        if gate.roadLabel:
            farther = (outside[0] - inward[0] * 2.4, outside[1] - inward[1] * 2.4)
            _text(msp, gate.roadLabel, mm_pt(*farther), anno * 1.05, layer=LAYER_ROAD)


def _draw_roads(msp: Any, plan: EvChargingStationPlan, anno: float) -> None:
    for road in plan.roads:
        pts = [(p.x, p.y) for p in road.polygon]
        if len(pts) >= 2:
            msp.add_lwpolyline(mm_pts(pts), close=True, dxfattribs={"layer": LAYER_ROAD})
            if road.label:
                cx, cy = _centroid(road.polygon)
                _text(msp, road.label, mm_pt(cx, cy), anno, layer=LAYER_ROAD)


def _draw_greenery(msp: Any, plan: EvChargingStationPlan, anno: float) -> None:
    for g in plan.greenery:
        pts = [(p.x, p.y) for p in g.polygon]
        if len(pts) < 3:
            continue
        msp.add_lwpolyline(mm_pts(pts), close=True, dxfattribs={"layer": LAYER_GREEN})
        try:
            hatch = msp.add_hatch(color=3, dxfattribs={"layer": LAYER_GREEN})
            hatch.set_pattern_fill("ANSI31", scale=800.0, angle=45.0)
            hatch.paths.add_polyline_path(mm_pts(pts), is_closed=True)
        except Exception:  # noqa: BLE001
            pass
        if g.label:
            cx, cy = _centroid(g.polygon)
            _text(msp, g.label, mm_pt(cx, cy), anno * 0.85, layer=LAYER_GREEN)


def _draw_aisles(msp: Any, plan: EvChargingStationPlan, anno: float) -> None:
    for aisle in plan.aisles:
        for line in aisle_marking_polylines(aisle):
            for seg in dash_segments(line, dash=0.9, gap=0.55):
                if len(seg) >= 2:
                    msp.add_lwpolyline(mm_pts(seg), dxfattribs={"layer": LAYER_ROAD})
        if aisle.centerline:
            mid = aisle.centerline[len(aisle.centerline) // 2]
            label = aisle.label or "过道"
            _text(msp, label, mm_pt(mid.x, mid.y), anno * 0.8, layer=LAYER_ROAD)


def _draw_buildings(msp: Any, plan: EvChargingStationPlan, anno: float) -> None:
    for b in plan.buildings:
        rect = b.rect
        msp.add_blockref(
            BLOCK_BUILDING,
            insert=mm_pt(rect.x, rect.y),
            dxfattribs={
                "layer": LAYER_SITE,
                "rotation": float(rect.angleDeg),
                "xscale": float(rect.w),
                "yscale": float(rect.h),
            },
        )
        pts = rect_corners(rect)
        cx = sum(p[0] for p in pts) / 4.0
        cy = sum(p[1] for p in pts) / 4.0
        if b.label:
            _text(msp, b.label, mm_pt(cx, cy), anno, layer=LAYER_ANNO)


def _draw_parking(msp: Any, plan: EvChargingStationPlan, anno: float) -> None:
    for row in plan.parkingRows:
        truck = float(row.stallLengthM) >= 10
        outset = charger_outset_m(row)
        first = True
        vehicle = BLOCK_TRUCK if truck else BLOCK_CAR
        side = (row.charger.side if row.charger else "head") or "head"
        for rect, i in expand_stalls(row):
            msp.add_lwpolyline(
                mm_pts(rect_corners(rect)),
                close=True,
                dxfattribs={"layer": LAYER_PARK},
            )
            cx, cy, rot = _vehicle_insert(rect, side)
            msp.add_blockref(
                vehicle,
                insert=mm_pt(cx, cy),
                dxfattribs={"layer": LAYER_VEHICLE, "rotation": rot},
            )
            if row.charger and row.charger.type != "none":
                hx, hy = charger_point(rect, row.charger.side, outset=outset)
                pile = BLOCK_AC_PILE if row.charger.type == "ac_14kw" else BLOCK_DC_PILE
                msp.add_blockref(
                    pile,
                    insert=mm_pt(hx, hy),
                    dxfattribs={"layer": LAYER_EQUIP},
                )
                no = row.charger.startNo + i
                _text(
                    msp,
                    str(no),
                    mm_pt(hx + 0.9, hy),
                    anno * 0.75,
                    layer=LAYER_ANNO,
                    align="LEFT",
                )
                if first:
                    caption = _CHARGER_LABELS.get(str(row.charger.type), row.labelPrefix)
                    if caption:
                        _text(
                            msp,
                            caption,
                            mm_pt(hx, hy + 1.3),
                            anno * 0.8,
                            layer=LAYER_ANNO,
                        )
                    first = False


def _draw_equipment(msp: Any, plan: EvChargingStationPlan, anno: float) -> None:
    for eq in plan.equipment:
        name = _EQ_BLOCKS.get(str(eq.type), BLOCK_TRANSFORMER)
        msp.add_blockref(
            name,
            insert=mm_pt(eq.x, eq.y),
            dxfattribs={"layer": LAYER_EQUIP},
        )
        label = (eq.label or "").strip()
        skip_kva = {"group_host", "fire_hydrant", "cable_well", "vent_grille"}
        if eq.capacityKva and eq.type not in skip_kva:
            kva = f"{eq.capacityKva:g}kVA"
            blob = label.replace(" ", "").lower()
            if not label:
                label = kva
            elif kva.lower() not in blob:
                label = f"{label}({kva})"
        if not label:
            label = _EQ_LABELS.get(str(eq.type), "")
        if label:
            _text(msp, label, mm_pt(eq.x, eq.y - 2.4), anno * 0.85, layer=LAYER_ANNO)


_EQ_BLOCKS = {
    "ring_cabinet": BLOCK_RING,
    "group_host": BLOCK_RING,
    "box_transformer": BLOCK_TRANSFORMER,
    "ring_box_transformer": BLOCK_TRANSFORMER,
    "lv_cabinet": BLOCK_LV,
    "hv_meter": BLOCK_METER,
    "dc_320kw": BLOCK_DC_PILE,
    "dc_160kw": BLOCK_DC_PILE,
    "dc_120kw": BLOCK_DC_PILE,
    "ac_14kw": BLOCK_AC_PILE,
    "fire_hydrant": BLOCK_HYDRANT,
    "cable_well": BLOCK_WELL,
    "vent_grille": BLOCK_VENT,
}
_EQ_LABELS = {
    "fire_hydrant": "消火栓",
    "cable_well": "电缆井",
    "vent_grille": "通风格栅",
    "lv_cabinet": "低压柜",
    "hv_meter": "计量柜",
}


def _draw_trenches(msp: Any, plan: EvChargingStationPlan, anno: float = 2200.0) -> None:
    for trench in plan.trenches:
        pts = [(p.x, p.y) for p in trench.polyline]
        if len(pts) < 2:
            continue
        msp.add_lwpolyline(mm_pts(pts), dxfattribs={"layer": LAYER_CABLE})
        label = (trench.label or "").strip() or "电缆沟"
        mid = pts[len(pts) // 2]
        _text(msp, label, mm_pt(mid[0], mid[1] + 1.2), max(80.0, anno * 0.7), layer=LAYER_CABLE)


def _draw_trees(msp: Any, plan: EvChargingStationPlan) -> None:
    for tree in plan.trees:
        msp.add_blockref(
            BLOCK_TREE,
            insert=mm_pt(tree.x, tree.y),
            dxfattribs={"layer": LAYER_GREEN},
        )


def _draw_dimensions(msp: Any, plan: EvChargingStationPlan) -> None:
    for dim in build_sheet_dimensions(plan):
        _add_aligned_dim(msp, dim)


def _add_aligned_dim(msp: Any, dim: DimSpec) -> None:
    try:
        entity = msp.add_aligned_dim(
            p1=mm_pt(*dim.p1),
            p2=mm_pt(*dim.p2),
            distance=mm(dim.offset_m),
            dimstyle=DIMSTYLE,
            dxfattribs={"layer": LAYER_DIM, "color": 3},
        )
        entity.render()
    except Exception:  # noqa: BLE001
        return


def _vehicle_insert(rect: RectM, side: str) -> tuple[float, float, float]:
    cx, cy, along, _across, _length, _width = _vehicle_frame(rect, side)
    rad = math.radians(rect.angleDeg)
    rx = along[0] * math.cos(rad) - along[1] * math.sin(rad)
    ry = along[0] * math.sin(rad) + along[1] * math.cos(rad)
    if abs(rad) > 1e-9:
        from api.services.layouts.symbols import _rot

        cx, cy = _rot(cx, cy, rect.x, rect.y, rad)
    rot = math.degrees(math.atan2(ry, rx))
    return cx, cy, rot


def _text(
    msp: Any,
    text: str,
    xy: tuple[float, float],
    height: float,
    *,
    layer: str = LAYER_ANNO,
    align: str = "CENTER",
) -> None:
    raw = (text or "").strip()
    if not raw:
        return
    entity = msp.add_text(
        raw[:48],
        height=max(80.0, float(height)),
        dxfattribs={"layer": layer, "style": STYLE_CN},
    )
    mapping = {
        "LEFT": TextEntityAlignment.MIDDLE_LEFT,
        "CENTER": TextEntityAlignment.MIDDLE_CENTER,
        "RIGHT": TextEntityAlignment.MIDDLE_RIGHT,
    }
    entity.set_placement(xy, align=mapping.get(align, TextEntityAlignment.MIDDLE_CENTER))


def _centroid(pts: list[PointM]) -> tuple[float, float]:
    return sum(p.x for p in pts) / len(pts), sum(p.y for p in pts) / len(pts)


def _gate_inward(side: str) -> tuple[float, float]:
    return {
        "south": (0.0, 1.0),
        "north": (0.0, -1.0),
        "west": (1.0, 0.0),
        "east": (-1.0, 0.0),
    }[side]


def _gate_ends(
    gate: GateSpec, width: float, height: float
) -> tuple[tuple[float, float], tuple[float, float]]:
    if gate.side == "south":
        return (gate.offsetM, 0.0), (gate.offsetM + gate.widthM, 0.0)
    if gate.side == "north":
        return (gate.offsetM, height), (gate.offsetM + gate.widthM, height)
    if gate.side == "west":
        return (0.0, gate.offsetM), (0.0, gate.offsetM + gate.widthM)
    return (width, gate.offsetM), (width, gate.offsetM + gate.widthM)


def _boundary_segments(
    width: float, height: float, gates: list[GateSpec]
) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    sides: list[tuple[str, tuple[float, float], tuple[float, float]]] = [
        ("south", (0.0, 0.0), (width, 0.0)),
        ("east", (width, 0.0), (width, height)),
        ("north", (width, height), (0.0, height)),
        ("west", (0.0, height), (0.0, 0.0)),
    ]
    by_side: dict[str, list[GateSpec]] = {}
    for g in gates:
        by_side.setdefault(g.side, []).append(g)
    segs: list[tuple[tuple[float, float], tuple[float, float]]] = []
    for side, start, end in sides:
        glist = by_side.get(side) or []
        if not glist:
            segs.append((start, end))
            continue
        cuts: list[tuple[float, tuple[float, float], tuple[float, float]]] = []
        for gate in glist:
            g0, g1 = _gate_ends(gate, width, height)
            d0 = math.hypot(g0[0] - start[0], g0[1] - start[1])
            d1 = math.hypot(g1[0] - start[0], g1[1] - start[1])
            a, b = (g0, g1) if d0 <= d1 else (g1, g0)
            cuts.append((math.hypot(a[0] - start[0], a[1] - start[1]), a, b))
        cuts.sort(key=lambda c: c[0])
        cursor = start
        for _d, a, b in cuts:
            segs.append((cursor, a))
            cursor = b
        segs.append((cursor, end))
    return segs
