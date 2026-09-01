"""布置 JSON → SVG 矢量图（与 PNG/DXF 同源几何）。"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4
from xml.sax.saxutils import escape

from api.services.layouts.pack import (
    charger_outset_m,
    charger_point,
    expand_stalls,
    plan_gates,
    prepare_plan,
    rect_corners,
)
from api.services.layouts.symbols import aisle_marking_polylines, dash_segments, vehicle_polylines
from api.services.layouts.schema import (
    EvChargingStationPlan,
    site_boundary_m,
    site_is_irregular,
)
from common.config import get_settings

_PAD_L, _PAD_R, _PAD_T, _PAD_B = 90.0, 110.0, 168.0, 220.0
_LEGEND = {
    "ring_cabinet": "环网柜",
    "box_transformer": "箱式变电站",
    "group_host": "群冲主机柜",
    "dc_320kw": "320kW直流充电桩",
    "dc_160kw": "160kW直流充电桩",
    "dc_120kw": "120kW直流充电桩",
    "ac_14kw": "14kW交流充电桩",
    "parking": "小轿车",
    "truck": "重卡",
    "greenery": "绿化",
    "tree": "树木",
}


def _poly_points(pts: list[tuple[float, float]]) -> str:
    return " ".join(f"{x:.2f},{y:.2f}" for x, y in pts)


def render_plan_svg(
    plan: EvChargingStationPlan, *, max_px: int = 1600, query: str = ""
) -> str:
    plan = prepare_plan(plan, gentle=True, lock_envelope=True, query=query)
    if query:
        from api.services.layouts.pack import sync_charger_annotations
        from api.services.layouts.revise import apply_query_charger_to_plan

        apply_query_charger_to_plan(plan, query)
        sync_charger_annotations(plan)
    style = plan.sheetStyle
    site_w, site_h = float(plan.site.widthM), float(plan.site.heightM)
    inner_w = max_px - _PAD_L - _PAD_R
    inner_h = max_px - _PAD_T - _PAD_B
    scale = max(4.0, min(inner_w / site_w, inner_h / site_h, 28.0))
    img_w = int(_PAD_L + site_w * scale + _PAD_R)
    img_h = int(_PAD_T + site_h * scale + _PAD_B)

    def xy(x: float, y: float) -> tuple[float, float]:
        return (_PAD_L + x * scale, _PAD_T + (site_h - y) * scale)

    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{img_w}" height="{img_h}" '
        f'viewBox="0 0 {img_w} {img_h}" fill="none" stroke="#000" stroke-width="1.2">',
        f'<rect x="8" y="8" width="{img_w - 16}" height="{img_h - 16}" stroke-width="3"/>',
        f'<rect x="14" y="14" width="{img_w - 28}" height="{img_h - 28}" stroke-width="1"/>',
    ]

    if style.showLegend:
        ly = 28.0
        parts.append('<g id="legend" stroke="#000" fill="#000" font-size="13">')
        parts.append('<text x="28" y="24" font-weight="600">图例</text>')
        for sym in plan.legend:
            label = _LEGEND.get(sym, sym)
            parts.append(f'<text x="28" y="{ly + 16}">{escape(label)}</text>')
            ly += 18
        parts.append("</g>")

    # roads / greenery
    for road in plan.roads:
        pts = [xy(p.x, p.y) for p in road.polygon]
        if len(pts) >= 3:
            parts.append(f'<polygon points="{_poly_points(pts)}" fill="none"/>')
            if road.label:
                cx = sum(p.x for p in road.polygon) / len(road.polygon)
                cy = sum(p.y for p in road.polygon) / len(road.polygon)
                sx, sy = xy(cx, cy)
                parts.append(
                    f'<text x="{sx:.1f}" y="{sy:.1f}" fill="#000" stroke="none" '
                    f'font-size="14" text-anchor="middle">{escape(road.label)}</text>'
                )
    for g in plan.greenery:
        pts = [xy(p.x, p.y) for p in g.polygon]
        if len(pts) >= 3:
            parts.append(
                f'<polygon points="{_poly_points(pts)}" fill="#f3f3f3" stroke="#000"/>'
            )

    # site
    if site_is_irregular(plan.site):
        poly = [xy(x, y) for x, y in site_boundary_m(plan.site)]
        if len(poly) >= 3:
            parts.append(
                f'<polygon points="{_poly_points(poly)}" fill="none" stroke-width="2.5"/>'
            )
    else:
        x0, y0 = xy(0, site_h)
        x1, y1 = xy(site_w, 0)
        parts.append(
            f'<rect x="{x0:.1f}" y="{y0:.1f}" width="{(x1 - x0):.1f}" height="{(y1 - y0):.1f}" '
            f'fill="none" stroke-width="2.5"/>'
        )

    for aisle in plan.aisles:
        for line in aisle_marking_polylines(aisle):
            for seg in dash_segments(line, dash=0.85, gap=0.5):
                if len(seg) < 2:
                    continue
                a, b = xy(*seg[0]), xy(*seg[1])
                parts.append(
                    f'<line x1="{a[0]:.1f}" y1="{a[1]:.1f}" x2="{b[0]:.1f}" y2="{b[1]:.1f}" '
                    f'stroke="#000" stroke-width="1"/>'
                )
        if aisle.centerline:
            mid = aisle.centerline[len(aisle.centerline) // 2]
            mx, my = xy(mid.x, mid.y)
            parts.append(
                f'<text x="{mx:.1f}" y="{my:.1f}" fill="#000" stroke="none" font-size="12" '
                f'text-anchor="middle">过道</text>'
            )

    for bld in plan.buildings:
        r = bld.rect
        corners = [xy(x, y) for x, y in rect_corners(r)]
        parts.append(f'<polygon points="{_poly_points(corners)}" fill="#eee" stroke="#000"/>')
        cx, cy = r.x + r.w / 2, r.y + r.h / 2
        sx, sy = xy(cx, cy)
        parts.append(
            f'<text x="{sx:.1f}" y="{sy:.1f}" fill="#000" stroke="none" font-size="13" '
            f'text-anchor="middle">{escape(bld.label)}</text>'
        )

    for row in plan.parkingRows:
        truck = float(row.stallLengthM) >= 10
        side = (row.charger.side if row.charger else "head") or "head"
        for rect, _idx in expand_stalls(row):
            pts = [xy(x, y) for x, y in rect_corners(rect)]
            parts.append(f'<polygon points="{_poly_points(pts)}" fill="none"/>')
            for ring in vehicle_polylines(rect, side, truck=truck):
                img = [xy(x, y) for x, y in ring]
                parts.append(f'<polygon points="{_poly_points(img)}" fill="none"/>')
        if row.charger and row.charger.type != "none":
            outset = charger_outset_m(row)
            for rect, idx in expand_stalls(row):
                px, py = charger_point(rect, row.charger.side, outset=outset)
                sx, sy = xy(px, py)
                s = max(2.0, min(0.28 * scale, min(rect.w, rect.h) * 0.12 * scale))
                parts.append(
                    f'<rect x="{sx - s:.1f}" y="{sy - s:.1f}" width="{2 * s:.1f}" height="{2 * s:.1f}" fill="#000"/>'
                )

    for eq in plan.equipment:
        sx, sy = xy(eq.x, eq.y)
        parts.append(f'<rect x="{sx - 10:.1f}" y="{sy - 8:.1f}" width="20" height="16" fill="#fff"/>')
        label = eq.label or eq.type
        parts.append(
            f'<text x="{sx:.1f}" y="{sy + 22:.1f}" fill="#000" stroke="none" font-size="12" '
            f'text-anchor="middle">{escape(label)}</text>'
        )

    for gate in plan_gates(plan):
        w, h = site_w, site_h
        if gate.side == "south":
            a, b = (gate.offsetM, 0.0), (gate.offsetM + gate.widthM, 0.0)
        elif gate.side == "north":
            a, b = (gate.offsetM, h), (gate.offsetM + gate.widthM, h)
        elif gate.side == "west":
            a, b = (0.0, gate.offsetM), (0.0, gate.offsetM + gate.widthM)
        else:
            a, b = (w, gate.offsetM), (w, gate.offsetM + gate.widthM)
        p0, p1 = xy(*a), xy(*b)
        parts.append(
            f'<line x1="{p0[0]:.1f}" y1="{p0[1]:.1f}" x2="{p1[0]:.1f}" y2="{p1[1]:.1f}" '
            f'stroke-width="4"/>'
        )

    if style.showNorthArrow:
        nx, ny = img_w - 70, 50
        parts.append(
            f'<g id="north"><polygon points="{nx},{ny - 18} {nx - 8},{ny + 8} {nx + 8},{ny + 8}" '
            f'fill="#000"/><text x="{nx}" y="{ny + 24}" fill="#000" stroke="none" '
            f'font-size="12" text-anchor="middle">N</text></g>'
        )

    if style.showTitleBlock:
        title = plan.titleBlock.title or "充电站平面布置图"
        parts.append(
            f'<text x="{img_w - 24}" y="{img_h - 36}" fill="#000" stroke="none" font-size="16" '
            f'text-anchor="end" font-weight="600">{escape(title)}</text>'
        )
        parts.append(
            f'<text x="{img_w - 24}" y="{img_h - 16}" fill="#333" stroke="none" font-size="12" '
            f'text-anchor="end">比例 {escape(style.scale)} · {escape(style.paper)}</text>'
        )

    parts.append("</svg>")
    return "\n".join(parts)


def write_plan_svg(
    plan: EvChargingStationPlan, dest: Path | None = None, *, query: str = ""
) -> Path:
    root = Path(get_settings().storage_root) / "layouts"
    root.mkdir(parents=True, exist_ok=True)
    path = dest or (root / f"{uuid4().hex[:12]}.svg")
    path.write_text(render_plan_svg(plan, query=query), encoding="utf-8")
    return path
