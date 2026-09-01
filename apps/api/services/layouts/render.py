"""把布置 JSON 画成黑白平面图 PNG（Pillow，无 CAD 依赖）。"""

from __future__ import annotations

import math
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from api.services.layouts.pack import (
    charger_outset_m,
    charger_point as _charger_point,
    expand_stalls,
    plan_gates,
    prepare_plan,
    rect_corners as _rect_corners,
)
from api.services.layouts.symbols import (
    aisle_marking_polylines,
    dash_segments,
    vehicle_polylines,
)
from api.services.layouts.schema import (
    BuildingSpec,
    EquipmentSpec,
    EvChargingStationPlan,
    GateSpec,
    ParkingRowSpec,
    PointM,
)

_PAD_L, _PAD_R, _PAD_T, _PAD_B = 90, 110, 168, 250
_MAX_PX = 1800
_DIM = (0, 140, 0)
_LEGEND_LABELS = {
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


class _Map:
    def __init__(self, plan: EvChargingStationPlan, scale: float) -> None:
        self.plan = plan
        self.scale = scale
        self.h = plan.site.heightM

    def xy(self, x: float, y: float) -> tuple[float, float]:
        return (_PAD_L + x * self.scale, _PAD_T + (self.h - y) * self.scale)

    def d(self, meters: float) -> float:
        return meters * self.scale


def _font(size: int) -> ImageFont.ImageFont:
    size = max(16, int(size))
    for path in (
        Path(r"C:\Windows\Fonts\msyh.ttc"),
        Path(r"C:\Windows\Fonts\msyhbd.ttc"),
        Path(r"C:\Windows\Fonts\simhei.ttf"),
        Path(r"C:\Windows\Fonts\simsun.ttc"),
        Path(r"C:\Windows\Fonts\msyh.ttf"),
        Path("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/System/Library/Fonts/PingFang.ttc"),
    ):
        if path.is_file():
            try:
                return ImageFont.truetype(str(path), size=size, index=0)
            except OSError:
                try:
                    return ImageFont.truetype(str(path), size=size)
                except OSError:
                    continue
    return ImageFont.load_default()


def _poly_img(mp: _Map, pts: list[tuple[float, float]]) -> list[tuple[float, float]]:
    return [mp.xy(x, y) for x, y in pts]


def _centroid(pts: list[PointM]) -> tuple[float, float]:
    return sum(p.x for p in pts) / len(pts), sum(p.y for p in pts) / len(pts)


def render_plan_png(
    plan: EvChargingStationPlan, *, max_px: int = _MAX_PX, query: str = ""
) -> bytes:
    # 出图前只做温和校正；query 在编号定稿后再涂桩型，避免 27–50 的 160kW 被整场 320kW 盖掉
    plan = prepare_plan(plan, gentle=True, lock_envelope=True, query=query)
    if query:
        from api.services.layouts.pack import sync_charger_annotations
        from api.services.layouts.revise import apply_query_charger_to_plan

        apply_query_charger_to_plan(plan, query)
        sync_charger_annotations(plan)
    site_w, site_h = plan.site.widthM, plan.site.heightM
    inner_w = max_px - _PAD_L - _PAD_R
    inner_h = max_px - _PAD_T - _PAD_B
    scale = min(inner_w / site_w, inner_h / site_h)
    scale = max(4.0, min(scale, 28.0))
    img_w = int(_PAD_L + site_w * scale + _PAD_R)
    img_h = int(_PAD_T + site_h * scale + _PAD_B)
    image = Image.new("RGB", (img_w, img_h), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    mp = _Map(plan, scale)
    base = max(16, int(min(img_w, img_h) * 0.016))
    font_s = _font(base)
    font_m = _font(base + 4)
    font_chrome = _font(16)

    draw.rectangle((8, 8, img_w - 9, img_h - 9), outline=(0, 0, 0), width=3)
    draw.rectangle((14, 14, img_w - 15, img_h - 15), outline=(0, 0, 0), width=1)

    _draw_legend(draw, plan, font_chrome)

    for road in plan.roads:
        pts = _poly_img(mp, [(p.x, p.y) for p in road.polygon])
        draw.polygon(pts, outline=(0, 0, 0))
        if road.label:
            cx, cy = _centroid(road.polygon)
            _text(draw, mp.xy(cx, cy), road.label, font_m, anchor="mm")

    for g in plan.greenery:
        pts = _poly_img(mp, [(p.x, p.y) for p in g.polygon])
        draw.polygon(pts, outline=(0, 0, 0))
        _stipple(draw, pts)
        if g.label:
            cx, cy = _centroid(g.polygon)
            _text(draw, mp.xy(cx, cy), g.label, font_s, anchor="mm")

    for b in plan.buildings:
        _draw_building(draw, mp, b, font_s)

    labeled_trench = False
    for trench in plan.trenches:
        pts = [mp.xy(p.x, p.y) for p in trench.polyline]
        if len(pts) < 2:
            continue
        draw.line(
            pts,
            fill=(0, 0, 0),
            width=max(2, min(6, int(mp.d(min(trench.widthMm / 1000.0, 0.55))))),
        )
        if trench.label and not labeled_trench:
            mid = pts[len(pts) // 2]
            _text(draw, (mid[0], mid[1] - 12), trench.label, font_s, anchor="mm")
            labeled_trench = True

    _draw_aisles(draw, mp, plan, font_s)

    for row in plan.parkingRows:
        _draw_parking_row(draw, mp, row, font_s)

    for eq in plan.equipment:
        _draw_equipment(draw, mp, eq, font_s)

    for tree in plan.trees:
        _draw_tree(draw, mp.xy(tree.x, tree.y), max(5, mp.d(0.7)))

    _draw_site_and_gate(draw, mp, plan, font_s)
    _draw_dimensions(draw, mp, plan, font_s)
    _draw_north(draw, img_w, font_m)
    _, fh = _text_size(font_chrome, "m")
    band_top = max(mp.xy(0, 0)[1], mp.xy(0, -1.2)[1]) + fh + 28
    title_x0 = _draw_title_block(draw, plan, img_w, img_h, font_chrome, band_top)
    _draw_notes(draw, plan, img_w, img_h, font_chrome, title_x0, band_top)

    buf = BytesIO()
    image.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _text(
    draw: ImageDraw.ImageDraw,
    xy: tuple[float, float],
    text: str,
    font: ImageFont.ImageFont,
    *,
    anchor: str = "lt",
) -> None:
    try:
        draw.text(xy, text, fill=(0, 0, 0), font=font, anchor=anchor)
    except (TypeError, ValueError, OSError):
        draw.text(xy, text, fill=(0, 0, 0), font=font)


def _text_size(font: ImageFont.ImageFont, text: str) -> tuple[int, int]:
    try:
        bbox = font.getbbox(text)
        return max(1, int(bbox[2] - bbox[0])), max(1, int(bbox[3] - bbox[1]))
    except Exception:
        size = int(getattr(font, "size", 16) or 16)
        return max(1, len(text) * size), size


def _fit_text(text: str, font: ImageFont.ImageFont, max_w: float) -> str:
    text = (text or "").strip()
    if not text or _text_size(font, text)[0] <= max_w:
        return text
    ell = "…"
    if _text_size(font, ell)[0] > max_w:
        return ""
    lo, hi = 0, len(text)
    best = ell
    while lo <= hi:
        mid = (lo + hi) // 2
        cand = text[:mid] + ell
        if _text_size(font, cand)[0] <= max_w:
            best = cand
            lo = mid + 1
        else:
            hi = mid - 1
    return best


def _wrap_to_width(text: str, font: ImageFont.ImageFont, max_w: float) -> list[str]:
    text = (text or "").strip()
    if not text:
        return [""]
    max_w = max(24.0, float(max_w))
    lines: list[str] = []
    cur = ""
    for ch in text:
        trial = cur + ch
        if _text_size(font, trial)[0] <= max_w:
            cur = trial
            continue
        if cur:
            lines.append(cur)
            cur = ch
        else:
            lines.append(ch)
            cur = ""
    if cur:
        lines.append(cur)
    return lines


def _stipple(draw: ImageDraw.ImageDraw, pts: list[tuple[float, float]]) -> None:
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    min_x, max_x = int(min(xs)), int(max(xs))
    min_y, max_y = int(min(ys)), int(max(ys))
    for x in range(min_x, max_x, 6):
        for y in range(min_y, max_y, 6):
            draw.point((x, y), fill=(80, 80, 80))


def _draw_building(
    draw: ImageDraw.ImageDraw, mp: _Map, b: BuildingSpec, font: ImageFont.ImageFont
) -> None:
    pts = _poly_img(mp, _rect_corners(b.rect))
    width = 1 if b.kind == "demolish" else 2
    draw.polygon(pts, outline=(0, 0, 0))
    if width == 1:
        draw.line([pts[0], pts[2]], fill=(0, 0, 0), width=1)
        draw.line([pts[1], pts[3]], fill=(0, 0, 0), width=1)
    cx = sum(p[0] for p in pts) / 4
    cy = sum(p[1] for p in pts) / 4
    _text(draw, (cx, cy), b.label, font, anchor="mm")


def _eq_caption(eq: EquipmentSpec) -> str:
    label = (eq.label or "").strip()
    if eq.type == "group_host":
        return label or "群冲主机柜"
    if not eq.capacityKva:
        return label
    kva = f"{eq.capacityKva:g}kVA"
    blob = label.replace(" ", "").replace("（", "(").replace("）", ")").lower()
    if not label:
        return kva
    if kva.lower() in blob:
        return label
    return f"{label}({kva})"


def _draw_aisles(
    draw: ImageDraw.ImageDraw,
    mp: _Map,
    plan: EvChargingStationPlan,
    font: ImageFont.ImageFont,
) -> None:
    for aisle in plan.aisles:
        for line in aisle_marking_polylines(aisle):
            for seg in dash_segments(line, dash=0.85, gap=0.5):
                if len(seg) < 2:
                    continue
                draw.line([mp.xy(*seg[0]), mp.xy(*seg[1])], fill=(0, 0, 0), width=1)
        if len(aisle.centerline) >= 2:
            mid = aisle.centerline[len(aisle.centerline) // 2]
            _text(draw, mp.xy(mid.x, mid.y), "过道", font, anchor="mm")


def _draw_vehicle(
    draw: ImageDraw.ImageDraw, mp: _Map, rect, charger_side: str, *, truck: bool
) -> None:
    rings = vehicle_polylines(rect, charger_side, truck=truck)
    for i, ring in enumerate(rings):
        pts = _poly_img(mp, ring)
        if len(pts) < 2:
            continue
        if i == 0 or (truck and i < 3):
            draw.polygon(pts, outline=(0, 0, 0))
        else:
            draw.line(pts + [pts[0]], fill=(0, 0, 0), width=1)


def _draw_parking_row(
    draw: ImageDraw.ImageDraw, mp: _Map, row: ParkingRowSpec, font: ImageFont.ImageFont
) -> None:
    truck = float(row.stallLengthM) >= 10
    outset = charger_outset_m(row)
    first_charger: tuple[int, int] | None = None
    side = (row.charger.side if row.charger else "head") or "head"
    for rect, i in expand_stalls(row):
        pts = _poly_img(mp, _rect_corners(rect))
        draw.polygon(pts, outline=(0, 0, 0))
        _draw_vehicle(draw, mp, rect, side, truck=truck)
        if row.charger and row.charger.type != "none":
            px, py = mp.xy(*_charger_point(rect, row.charger.side, outset=outset))
            s = max(2.0, min(mp.d(0.28), mp.d(min(rect.w, rect.h) * 0.12)))
            draw.rectangle((px - s, py - s, px + s, py + s), fill=(0, 0, 0))
            no = row.charger.startNo + i
            _text(draw, (px + s + 2, py), str(no), font, anchor="lm")
            if first_charger is None:
                first_charger = (px, py - s - 2)
    if first_charger is not None:
        label = row.labelPrefix
        if row.charger and row.charger.type in _LEGEND_LABELS:
            label = _LEGEND_LABELS[row.charger.type]
        if label:
            _text(draw, first_charger, label, font, anchor="mb")


def _draw_equipment(
    draw: ImageDraw.ImageDraw,
    mp: _Map,
    eq: EquipmentSpec,
    font: ImageFont.ImageFont,
) -> None:
    x, y = mp.xy(eq.x, eq.y)
    if eq.type in {"dc_320kw", "dc_160kw", "dc_120kw", "ac_14kw"}:
        s = max(3, mp.d(0.5))
        if eq.type in {"dc_320kw", "dc_160kw", "dc_120kw"}:
            draw.rectangle((x - s, y - s, x + s, y + s), fill=(0, 0, 0))
        else:
            draw.rectangle((x - s, y - s, x + s, y + s), outline=(0, 0, 0), width=2)
    elif eq.type == "ring_cabinet":
        s = max(8, mp.d(1.4))
        draw.rectangle((x - s, y - s, x + s, y + s), outline=(0, 0, 0), width=2)
        _text(draw, (x, y), "HW", font, anchor="mm")
    elif eq.type == "group_host":
        w, h = max(16, mp.d(2.8)), max(11, mp.d(1.7))
        draw.rectangle((x - w / 2, y - h / 2, x + w / 2, y + h / 2), outline=(0, 0, 0), width=2)
        _text(draw, (x, y), "群冲", font, anchor="mm")
    else:
        w, h = max(18, mp.d(3.2)), max(12, mp.d(2.2))
        draw.rectangle((x - w / 2, y - h / 2, x + w / 2, y + h / 2), outline=(0, 0, 0), width=2)
        draw.polygon(
            [(x, y - h * 0.28), (x - w * 0.22, y + h * 0.2), (x + w * 0.22, y + h * 0.2)],
            outline=(0, 0, 0),
        )
    label = _eq_caption(eq)
    if label:
        _text(draw, (x, y + max(14, mp.d(1.6))), label, font, anchor="mt")


def _draw_tree(draw: ImageDraw.ImageDraw, xy: tuple[float, float], r: float) -> None:
    x, y = xy
    draw.ellipse((x - r, y - r, x + r, y + r), outline=(0, 0, 0))
    draw.line((x, y - r * 0.2, x, y + r * 0.7), fill=(0, 0, 0), width=1)
    draw.arc((x - r * 0.7, y - r * 0.7, x + r * 0.2, y + r * 0.1), 200, 40, fill=(0, 0, 0))


def _gate_inward(side: str) -> tuple[float, float]:
    return {
        "south": (0.0, 1.0),
        "north": (0.0, -1.0),
        "west": (1.0, 0.0),
        "east": (-1.0, 0.0),
    }[side]


def _gate_ends(gate: GateSpec, width: float, height: float) -> tuple[tuple[float, float], tuple[float, float]]:
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


def _draw_arrow(
    draw: ImageDraw.ImageDraw,
    mp: _Map,
    tip: tuple[float, float],
    direction: tuple[float, float],
    length: float = 2.4,
) -> None:
    nx, ny = direction
    tail = (tip[0] - nx * length, tip[1] - ny * length)
    p_tip, p_tail = mp.xy(*tip), mp.xy(*tail)
    draw.line([p_tail, p_tip], fill=(0, 0, 0), width=2)
    px, py = -ny, nx
    left = (tip[0] - nx * 0.7 + px * 0.35, tip[1] - ny * 0.7 + py * 0.35)
    right = (tip[0] - nx * 0.7 - px * 0.35, tip[1] - ny * 0.7 - py * 0.35)
    draw.polygon([p_tip, mp.xy(*left), mp.xy(*right)], outline=(0, 0, 0), fill=(0, 0, 0))


def _draw_site_and_gate(
    draw: ImageDraw.ImageDraw, mp: _Map, plan: EvChargingStationPlan, font: ImageFont.ImageFont
) -> None:
    from api.services.layouts.schema import site_boundary_m, site_is_irregular

    w, h = plan.site.widthM, plan.site.heightM
    gates = plan_gates(plan)
    if site_is_irregular(plan.site):
        pts = [mp.xy(x, y) for x, y in site_boundary_m(plan.site)]
        if len(pts) >= 2:
            draw.line(pts, fill=(0, 0, 0), width=3)
    else:
        for a, b in _boundary_segments(w, h, gates):
            if math.hypot(b[0] - a[0], b[1] - a[1]) < 0.08:
                continue
            draw.line([mp.xy(*a), mp.xy(*b)], fill=(0, 0, 0), width=2)
    for i, gate in enumerate(gates):
        g0, g1 = _gate_ends(gate, w, h)
        inward = _gate_inward(gate.side)
        jamb = 1.4
        for end in (g0, g1):
            inner = (end[0] + inward[0] * jamb, end[1] + inward[1] * jamb)
            draw.line([mp.xy(*end), mp.xy(*inner)], fill=(0, 0, 0), width=2)
        mid = ((g0[0] + g1[0]) / 2, (g0[1] + g1[1]) / 2)
        outside = (mid[0] - inward[0] * 2.2, mid[1] - inward[1] * 2.2)
        _text(draw, mp.xy(*outside), gate.label or "出入口", font, anchor="mm")
        _draw_arrow(
            draw,
            mp,
            (mid[0] + inward[0] * 1.6, mid[1] + inward[1] * 1.6),
            inward,
        )
        if i == 0 and gate.roadLabel:
            farther = (outside[0] - inward[0] * 1.6, outside[1] - inward[1] * 1.6)
            _text(draw, mp.xy(*farther), gate.roadLabel, font, anchor="mm")


def _text_color(
    draw: ImageDraw.ImageDraw,
    xy: tuple[float, float],
    text: str,
    font: ImageFont.ImageFont,
    fill: tuple[int, int, int],
    *,
    anchor: str = "lt",
) -> None:
    try:
        draw.text(xy, text, fill=fill, font=font, anchor=anchor)
    except (TypeError, ValueError, OSError):
        draw.text(xy, text, fill=fill, font=font)


def _draw_dimensions(
    draw: ImageDraw.ImageDraw, mp: _Map, plan: EvChargingStationPlan, font: ImageFont.ImageFont
) -> None:
    w, h = plan.site.widthM, plan.site.heightM
    south = [mp.xy(0, -1.2), mp.xy(w, -1.2)]
    west = [mp.xy(-1.2, 0), mp.xy(-1.2, h)]
    draw.line(south, fill=_DIM, width=2)
    draw.line(west, fill=_DIM, width=2)
    mid_s = mp.xy(w / 2, -1.2)
    mid_w = mp.xy(-1.2, h / 2)
    _text_color(draw, (mid_s[0], mid_s[1] + 10), f"{w:g}m", font, _DIM, anchor="mt")
    _text_color(draw, (mid_w[0] - 6, mid_w[1]), f"{h:g}m", font, _DIM, anchor="rm")
    trucks = next((r for r in plan.parkingRows if r.stallLengthM >= 10), None)
    cars = next((r for r in plan.parkingRows if r.stallLengthM < 10), None)
    if trucks:
        p = mp.xy(trucks.origin.x + trucks.stallWidthM / 2, trucks.origin.y)
        _text_color(
            draw,
            (p[0], p[1] + 8),
            f"{trucks.stallWidthM:g}×{trucks.stallLengthM:g}m",
            font,
            _DIM,
            anchor="mt",
        )
    if cars:
        p = mp.xy(cars.origin.x + cars.stallWidthM / 2, cars.origin.y)
        _text_color(
            draw,
            (p[0], p[1] + 8),
            f"{cars.stallWidthM:g}×{cars.stallLengthM:g}m",
            font,
            _DIM,
            anchor="mt",
        )


def _draw_north(
    draw: ImageDraw.ImageDraw, img_w: int, font: ImageFont.ImageFont
) -> None:
    x, y = img_w - 48, 48
    draw.polygon([(x, y - 16), (x - 7, y + 8), (x, y + 2), (x + 7, y + 8)], outline=(0, 0, 0))
    _text(draw, (x, y - 22), "北", font, anchor="mm")


def _draw_title_block(
    draw: ImageDraw.ImageDraw,
    plan: EvChargingStationPlan,
    img_w: int,
    img_h: int,
    font: ImageFont.ImageFont,
    band_top: float,
) -> float:
    tb = plan.titleBlock
    rows = [
        ("工程名称", tb.project or tb.title or "充电站平面布置"),
        ("图号", tb.sheetNo or "001"),
        ("设计", ""),
        ("审核", ""),
        ("日期", ""),
    ]
    _, fh = _text_size(font, "图")
    row_h = max(22, fh + 10)
    key_w = max(_text_size(font, k)[0] for k, _ in rows) + 16
    val_w = 228
    table_w = key_w + val_w
    x1 = img_w - 22
    x0 = x1 - table_w
    y1 = img_h - 22
    y0 = y1 - row_h * len(rows)
    if y0 < band_top:
        y0 = band_top
        row_h = max(18, (y1 - y0) / len(rows))
    draw.rectangle((x0, y0, x1, y1), outline=(0, 0, 0), width=1)
    for i, (k, v) in enumerate(rows):
        y = y0 + i * row_h
        if i:
            draw.line([(x0, y), (x1, y)], fill=(0, 0, 0), width=1)
        draw.line([(x0 + key_w, y), (x0 + key_w, min(y + row_h, y1))], fill=(0, 0, 0), width=1)
        cy = y + row_h / 2
        _text(draw, (x0 + 6, cy), _fit_text(k, font, key_w - 10), font, anchor="lm")
        fitted = _fit_text(str(v), font, val_w - 20)
        if fitted:
            _text(draw, (x0 + key_w + 8, cy), fitted, font, anchor="lm")
    return x0


def _draw_notes(
    draw: ImageDraw.ImageDraw,
    plan: EvChargingStationPlan,
    img_w: int,
    img_h: int,
    font: ImageFont.ImageFont,
    title_x0: float,
    band_top: float,
) -> None:
    notes = [str(n).strip() for n in (plan.notes or []) if str(n).strip()]
    if not notes:
        return
    _, fh = _text_size(font, "设")
    line_h = max(18, fh + 6)
    x0 = 22
    x1 = max(x0 + 180, min(title_x0 - 10, img_w * 0.64))
    y0 = band_top
    y1 = img_h - 22
    if y1 - y0 < line_h * 3:
        return
    draw.rectangle((x0, y0, x1, y1), outline=(0, 0, 0), width=1)
    _text(draw, (x0 + 8, y0 + 6), "设计说明", font)
    inner_w = x1 - x0 - 16
    lines: list[str] = []
    for note in notes[:6]:
        lines.extend(_wrap_to_width(note, font, inner_w))
    max_body = max(1, int((y1 - y0 - 12 - line_h) / line_h))
    for i, line in enumerate(lines[:max_body]):
        _text(draw, (x0 + 8, y0 + 6 + line_h + i * line_h), line, font)


def _draw_legend_mark(
    draw: ImageDraw.ImageDraw, sym: str, cx: float, cy: float
) -> None:
    if sym == "ring_cabinet":
        draw.rectangle((cx - 8, cy - 7, cx + 8, cy + 7), outline=(0, 0, 0), width=1)
    elif sym == "box_transformer":
        draw.rectangle((cx - 10, cy - 7, cx + 10, cy + 7), outline=(0, 0, 0), width=1)
        draw.polygon([(cx, cy - 4), (cx - 5, cy + 5), (cx + 5, cy + 5)], outline=(0, 0, 0))
    elif sym == "group_host":
        draw.rectangle((cx - 10, cy - 6, cx + 10, cy + 6), outline=(0, 0, 0), width=1)
    elif sym in {"dc_320kw", "dc_160kw", "dc_120kw"}:
        draw.rectangle((cx - 5, cy - 5, cx + 5, cy + 5), fill=(0, 0, 0))
    elif sym == "ac_14kw":
        draw.rectangle((cx - 5, cy - 5, cx + 5, cy + 5), outline=(0, 0, 0), width=1)
    elif sym == "parking":
        body = [
            (cx - 12, cy),
            (cx - 11, cy - 3),
            (cx - 8, cy - 5),
            (cx + 3, cy - 5),
            (cx + 9, cy - 3),
            (cx + 12, cy),
            (cx + 9, cy + 3),
            (cx + 3, cy + 5),
            (cx - 8, cy + 5),
            (cx - 11, cy + 3),
        ]
        draw.polygon(body, outline=(0, 0, 0))
        draw.polygon(
            [
                (cx + 1, cy - 3),
                (cx + 1, cy + 3),
                (cx + 6, cy + 1),
                (cx + 6, cy - 1),
            ],
            outline=(0, 0, 0),
        )
        draw.polygon(
            [
                (cx - 6, cy - 1),
                (cx - 6, cy + 1),
                (cx - 2, cy + 3),
                (cx - 2, cy - 3),
            ],
            outline=(0, 0, 0),
        )
    elif sym == "truck":
        draw.rectangle((cx - 11, cy - 4, cx + 2, cy + 4), outline=(0, 0, 0))
        draw.polygon(
            [
                (cx + 2, cy - 5),
                (cx + 8, cy - 4),
                (cx + 11, cy),
                (cx + 8, cy + 4),
                (cx + 2, cy + 5),
            ],
            outline=(0, 0, 0),
        )
    elif sym == "greenery":
        draw.rectangle((cx - 9, cy - 7, cx + 9, cy + 7), outline=(0, 0, 0), width=1)
        for i in range(int(cx - 6), int(cx + 7), 4):
            draw.point((i, cy), fill=(0, 0, 0))
    elif sym == "tree":
        draw.ellipse((cx - 7, cy - 7, cx + 7, cy + 7), outline=(0, 0, 0))
    else:
        draw.rectangle((cx - 5, cy - 5, cx + 5, cy + 5), outline=(0, 0, 0), width=1)


def _draw_legend(
    draw: ImageDraw.ImageDraw, plan: EvChargingStationPlan, font: ImageFont.ImageFont
) -> None:
    items = [str(s) for s in (plan.legend or []) if s]
    if not items:
        return
    _, fh = _text_size(font, "图")
    row_h = max(20, fh + 8)
    labels = [_LEGEND_LABELS.get(sym, str(sym)) for sym in items]
    mark_w = 28
    n = len(items)
    cols = 2 if n > 4 else 1
    pad_x, pad_y = 10, 6
    title_h = row_h

    def _legend_geom(col_count: int) -> tuple[int, list[int], int, int, int]:
        row_count = math.ceil(n / col_count)
        col_gap = 24 if col_count > 1 else 0
        widths = [mark_w + 16] * col_count
        for i, lb in enumerate(labels):
            c = i % col_count
            widths[c] = max(widths[c], mark_w + _text_size(font, lb)[0] + 16)
        width = pad_x * 2 + sum(widths) + col_gap * (col_count - 1)
        height = pad_y + title_h + row_count * row_h + pad_y
        return row_count, widths, col_gap, width, height

    _, col_ws, gap, box_w, box_h = _legend_geom(cols)
    x0, y0 = 22, 20
    if y0 + box_h > _PAD_T - 8:
        cols = 2
        _, col_ws, gap, box_w, box_h = _legend_geom(cols)
    draw.rectangle((x0, y0, x0 + box_w, y0 + box_h), outline=(0, 0, 0), width=1)
    _text(draw, (x0 + pad_x, y0 + pad_y), "图例", font)
    col_origin = [x0 + pad_x]
    for c in range(1, cols):
        col_origin.append(col_origin[-1] + col_ws[c - 1] + gap)
    for i, (sym, label) in enumerate(zip(items, labels)):
        r, c = divmod(i, cols)
        cx = col_origin[c] + 12
        cy = y0 + pad_y + title_h + r * row_h + row_h / 2
        _draw_legend_mark(draw, sym, cx, cy)
        _text(draw, (cx + 16, cy), label, font, anchor="lm")
