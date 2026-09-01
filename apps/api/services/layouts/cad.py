"""把装箱后的平面布置写成 DXF，并可将 DXF 栅格化成 PNG（不依赖 AutoCAD）。"""

from __future__ import annotations

import math
from io import BytesIO, StringIO
from pathlib import Path
from uuid import uuid4

from PIL import Image, ImageDraw, ImageFont

from api.services.layouts.pack import (
    _CHARGER_LABELS,
    charger_outset_m,
    charger_point,
    expand_stalls,
    plan_gates,
    rect_corners,
)
from api.services.layouts.symbols import aisle_marking_polylines, dash_segments, vehicle_polylines
from api.services.layouts.schema import EvChargingStationPlan, GateSpec, PointM
from common.config import get_settings

_CAD_EXTS = frozenset({"dxf"})
_MAX_PX = 1800
_PAD = 48


def is_cad_path(path: str | Path) -> bool:
    return Path(path).suffix.lower().lstrip(".") in _CAD_EXTS


def plan_to_dxf_bytes(plan: EvChargingStationPlan) -> bytes:
    import ezdxf

    doc = ezdxf.new(setup=True)
    doc.header["$INSUNITS"] = 6  # meters
    msp = doc.modelspace()
    for name, color in (
        ("SITE", 7),
        ("BUILDING", 8),
        ("PARKING", 4),
        ("AISLE", 2),
        ("EQUIP", 1),
        ("CABLE", 5),
        ("GREEN", 3),
        ("ANNO", 7),
    ):
        if name not in doc.layers:
            doc.layers.add(name, color=color)

    w, h = plan.site.widthM, plan.site.heightM
    gates = plan_gates(plan)
    from api.services.layouts.schema import site_boundary_m, site_is_irregular

    if site_is_irregular(plan.site):
        poly = site_boundary_m(plan.site)
        if len(poly) >= 3:
            msp.add_lwpolyline(poly, close=True, dxfattribs={"layer": "SITE"})
    else:
        for a, b in _boundary_segments(w, h, gates):
            if math.hypot(b[0] - a[0], b[1] - a[1]) < 0.08:
                continue
            msp.add_line(a, b, dxfattribs={"layer": "SITE"})
    for gate in gates:
        g0, g1 = _gate_ends(gate, w, h)
        inward = _gate_inward(gate.side)
        for end in (g0, g1):
            inner = (end[0] + inward[0] * 1.4, end[1] + inward[1] * 1.4)
            msp.add_line(end, inner, dxfattribs={"layer": "SITE"})
        mid = ((g0[0] + g1[0]) / 2, (g0[1] + g1[1]) / 2)
        outside = (mid[0] - inward[0] * 2.2, mid[1] - inward[1] * 2.2)
        _add_text(msp, gate.label or "出入口", outside, height=1.4)

    for road in plan.roads:
        pts = [(p.x, p.y) for p in road.polygon]
        if len(pts) >= 2:
            msp.add_lwpolyline(pts, close=True, dxfattribs={"layer": "SITE"})
            if road.label:
                cx, cy = _centroid(road.polygon)
                _add_text(msp, road.label, (cx, cy), height=1.6)

    for g in plan.greenery:
        pts = [(p.x, p.y) for p in g.polygon]
        if len(pts) >= 3:
            msp.add_lwpolyline(pts, close=True, dxfattribs={"layer": "GREEN"})
            if g.label:
                cx, cy = _centroid(g.polygon)
                _add_text(msp, g.label, (cx, cy), height=1.2)

    for aisle in plan.aisles:
        for line in aisle_marking_polylines(aisle):
            for seg in dash_segments(line, dash=0.9, gap=0.55):
                if len(seg) >= 2:
                    msp.add_lwpolyline(seg, dxfattribs={"layer": "AISLE"})
        if aisle.centerline:
            mid = aisle.centerline[len(aisle.centerline) // 2]
            _add_text(msp, "过道", (mid.x, mid.y), height=1.0)

    for b in plan.buildings:
        pts = rect_corners(b.rect)
        msp.add_lwpolyline(pts, close=True, dxfattribs={"layer": "BUILDING"})
        cx = sum(p[0] for p in pts) / 4
        cy = sum(p[1] for p in pts) / 4
        _add_text(msp, b.label, (cx, cy), height=1.8)

    for row in plan.parkingRows:
        truck = float(row.stallLengthM) >= 10
        outset = charger_outset_m(row)
        first = True
        for rect, i in expand_stalls(row):
            pts = rect_corners(rect)
            msp.add_lwpolyline(pts, close=True, dxfattribs={"layer": "PARKING"})
            side = (row.charger.side if row.charger else "head") or "head"
            for ring in vehicle_polylines(rect, side, truck=truck):
                if len(ring) >= 2:
                    msp.add_lwpolyline(ring, close=True, dxfattribs={"layer": "PARKING"})
            if row.charger and row.charger.type != "none":
                hx, hy = charger_point(rect, row.charger.side, outset=outset)
                s = 0.45
                msp.add_lwpolyline(
                    [(hx - s, hy - s), (hx + s, hy - s), (hx + s, hy + s), (hx - s, hy + s)],
                    close=True,
                    dxfattribs={"layer": "EQUIP"},
                )
                no = row.charger.startNo + i
                _add_text(msp, str(no), (hx + 0.8, hy), height=0.9)
                if first:
                    caption = _CHARGER_LABELS.get(str(row.charger.type), row.labelPrefix)
                    if caption:
                        _add_text(msp, caption, (hx, hy + 1.2), height=0.9)
                    first = False

    for eq in plan.equipment:
        s = 1.6 if eq.type == "ring_cabinet" else 1.8 if eq.type == "group_host" else 2.2
        msp.add_lwpolyline(
            [
                (eq.x - s, eq.y - s * 0.7),
                (eq.x + s, eq.y - s * 0.7),
                (eq.x + s, eq.y + s * 0.7),
                (eq.x - s, eq.y + s * 0.7),
            ],
            close=True,
            dxfattribs={"layer": "EQUIP"},
        )
        label = (eq.label or "").strip()
        if eq.capacityKva and eq.type != "group_host":
            kva = f"{eq.capacityKva:g}kVA"
            blob = label.replace(" ", "").lower()
            if not label:
                label = kva
            elif kva.lower() not in blob:
                label = f"{label}({kva})"
        if label:
            _add_text(msp, label, (eq.x, eq.y - s), height=1.1)

    for trench in plan.trenches:
        pts = [(p.x, p.y) for p in trench.polyline]
        if len(pts) >= 2:
            msp.add_lwpolyline(pts, dxfattribs={"layer": "CABLE"})

    for tree in plan.trees:
        msp.add_circle((tree.x, tree.y), 0.7, dxfattribs={"layer": "GREEN"})

    _add_text(msp, plan.titleBlock.title or "充电站平面布置图", (w * 0.5, -3.2), height=2.2)
    text = StringIO()
    doc.write(text)
    return text.getvalue().encode("utf-8")


def write_plan_dxf(plan: EvChargingStationPlan, dest: Path | None = None) -> Path:
    if dest is None:
        root = Path(get_settings().storage_root).expanduser().resolve() / "layouts"
        root.mkdir(parents=True, exist_ok=True)
        dest = root / f"{uuid4().hex[:12]}.dxf"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(plan_to_dxf_bytes(plan))
    return dest


def rasterize_dxf(path: str | Path, *, max_px: int = _MAX_PX) -> bytes:
    """把 CAD-MCP / 程序写出的 DXF 画成 PNG，供对话展示。"""
    import ezdxf

    src = Path(path).expanduser().resolve()
    if not src.is_file():
        raise FileNotFoundError(str(src))
    doc = ezdxf.readfile(str(src))
    msp = doc.modelspace()
    lines: list[tuple[tuple[float, float], tuple[float, float]]] = []
    circles: list[tuple[float, float, float]] = []
    texts: list[tuple[float, float, str]] = []
    for entity in msp:
        kind = entity.dxftype()
        if kind == "LINE":
            lines.append(((float(entity.dxf.start.x), float(entity.dxf.start.y)),
                          (float(entity.dxf.end.x), float(entity.dxf.end.y))))
        elif kind == "LWPOLYLINE":
            pts = [(float(p[0]), float(p[1])) for p in entity]
            if len(pts) >= 2:
                seq = list(pts)
                if bool(entity.closed) and seq[0] != seq[-1]:
                    seq.append(seq[0])
                for a, b in zip(seq, seq[1:]):
                    lines.append((a, b))
        elif kind == "POLYLINE":
            pts = [
                (float(v.dxf.location.x), float(v.dxf.location.y)) for v in entity.vertices
            ]
            if len(pts) >= 2:
                seq = list(pts)
                if bool(entity.is_closed) and seq[0] != seq[-1]:
                    seq.append(seq[0])
                for a, b in zip(seq, seq[1:]):
                    lines.append((a, b))
        elif kind == "CIRCLE":
            c = entity.dxf.center
            circles.append((float(c.x), float(c.y), float(entity.dxf.radius)))
        elif kind == "ARC":
            c = entity.dxf.center
            circles.append((float(c.x), float(c.y), float(entity.dxf.radius)))
        elif kind in {"TEXT", "MTEXT"}:
            ins = entity.dxf.insert
            raw = entity.plain_text() if kind == "MTEXT" else str(entity.dxf.text or "")
            if raw.strip():
                texts.append((float(ins.x), float(ins.y), raw.strip()[:40]))

    xs = [p[0] for a, b in lines for p in (a, b)]
    ys = [p[1] for a, b in lines for p in (a, b)]
    xs.extend(c[0] for c in circles)
    ys.extend(c[1] for c in circles)
    xs.extend(t[0] for t in texts)
    ys.extend(t[1] for t in texts)
    if not xs or not ys:
        image = Image.new("RGB", (640, 360), (255, 255, 255))
        ImageDraw.Draw(image).text((24, 24), "空图纸", fill=(0, 0, 0))
        buf = BytesIO()
        image.save(buf, format="PNG")
        return buf.getvalue()

    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    span_x = max(max_x - min_x, 1.0)
    span_y = max(max_y - min_y, 1.0)
    scale = min((max_px - 2 * _PAD) / span_x, (max_px - 2 * _PAD) / span_y)
    scale = max(2.0, min(scale, 28.0))
    img_w = int(2 * _PAD + span_x * scale)
    img_h = int(2 * _PAD + span_y * scale)
    image = Image.new("RGB", (img_w, img_h), (255, 255, 255))
    draw = ImageDraw.Draw(image)

    def xy(x: float, y: float) -> tuple[float, float]:
        return (_PAD + (x - min_x) * scale, _PAD + (max_y - y) * scale)

    for a, b in lines:
        draw.line([xy(*a), xy(*b)], fill=(0, 0, 0), width=1)
    for x, y, r in circles:
        p0 = xy(x - r, y + r)
        p1 = xy(x + r, y - r)
        draw.ellipse([p0, p1], outline=(0, 0, 0))
    font = _font(12)
    for x, y, label in texts:
        draw.text(xy(x, y), label, fill=(0, 0, 0), font=font)
    buf = BytesIO()
    image.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _add_text(msp: object, text: str, xy: tuple[float, float], *, height: float) -> None:
    if not text.strip():
        return
    msp.add_text(  # type: ignore[attr-defined]
        text.strip()[:48],
        dxfattribs={"height": height, "layer": "ANNO", "insert": xy},
    )


def _centroid(pts: list[PointM]) -> tuple[float, float]:
    return sum(p.x for p in pts) / len(pts), sum(p.y for p in pts) / len(pts)


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


def _font(size: int) -> ImageFont.ImageFont:
    for path in (
        Path(r"C:\Windows\Fonts\msyh.ttc"),
        Path(r"C:\Windows\Fonts\simhei.ttf"),
        Path("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"),
    ):
        if path.is_file():
            try:
                return ImageFont.truetype(str(path), size)
            except OSError:
                continue
    return ImageFont.load_default()
