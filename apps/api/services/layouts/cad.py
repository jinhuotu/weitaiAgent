"""把装箱后的平面布置写成 DXF，并可将 DXF 栅格化成 PNG（不依赖 AutoCAD）。"""

from __future__ import annotations

from io import BytesIO, StringIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from api.services.layouts.cad_export import plan_to_dxf_bytes, plan_to_dxf_doc
from api.services.layouts.cad_export.files import layout_stored_name
from api.services.layouts.cad_export.layers import LAYER_DIM, LAYER_META, LAYOUT_NAME
from api.services.layouts.cad_export.reader import plan_from_dxf
from api.services.layouts.schema import EvChargingStationPlan
from common.config import get_settings

_CAD_EXTS = frozenset({"dxf"})
_MAX_PX = 1800
_PAD = 16
_DIM_COLOR = (16, 140, 48)

__all__ = [
    "is_cad_path",
    "plan_to_dxf_bytes",
    "write_plan_dxf",
    "rasterize_dxf",
    "rasterize_doc",
    "export_plan_cad",
    "plan_from_dxf",
]


def is_cad_path(path: str | Path) -> bool:
    return Path(path).suffix.lower().lstrip(".") in _CAD_EXTS


def write_plan_dxf(plan: EvChargingStationPlan, dest: Path | None = None) -> Path:
    if dest is None:
        root = Path(get_settings().storage_root).expanduser().resolve() / "layouts"
        root.mkdir(parents=True, exist_ok=True)
        dest = root / layout_stored_name(plan, ext="dxf")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(plan_to_dxf_bytes(plan))
    return dest


def export_plan_cad(
    plan: EvChargingStationPlan,
    dest: Path,
    *,
    query: str = "",
    max_px: int = _MAX_PX,
) -> tuple[Path, bytes]:
    """一次出 DXF + 图纸空间 PNG 预览。"""
    doc = plan_to_dxf_doc(plan, query=query)
    dest.parent.mkdir(parents=True, exist_ok=True)
    buf = StringIO()
    doc.write(buf)
    dest.write_bytes(buf.getvalue().encode("utf-8"))
    return dest, rasterize_doc(doc, max_px=max_px)


def rasterize_dxf(path: str | Path, *, max_px: int = _MAX_PX) -> bytes:
    """把 CAD-MCP / 程序写出的 DXF 画成 PNG，供对话展示。优先图纸空间。"""
    import ezdxf

    src = Path(path).expanduser().resolve()
    if not src.is_file():
        raise FileNotFoundError(str(src))
    return rasterize_doc(ezdxf.readfile(str(src)), max_px=max_px)


def rasterize_doc(doc: object, *, max_px: int = _MAX_PX) -> bytes:
    layout = _preview_layout(doc)
    if layout is not None and layout.name != "Model":
        primitives = _paperspace_primitives(doc, layout)
    else:
        primitives = _collect_primitives(doc.modelspace())  # type: ignore[arg-type]
    return _paint(primitives, max_px=max_px)


def _preview_layout(doc: object):
    layouts = getattr(doc, "layouts", None)
    if layouts is None:
        return None
    try:
        active = layouts.active_layout()
        if active is not None and getattr(active, "name", "") != "Model":
            return active
    except Exception:  # noqa: BLE001
        pass
    for layout in layouts:
        if getattr(layout, "name", "") == LAYOUT_NAME:
            return layout
    return getattr(doc, "modelspace", lambda: None)()


def _paperspace_primitives(doc: object, psp: object) -> list[tuple]:
    out: list[tuple] = []
    viewports: list[object] = []
    native: list[object] = []
    for entity in psp:  # type: ignore[union-attr]
        if entity.dxftype() == "VIEWPORT" and int(getattr(entity.dxf, "status", 0) or 0) >= 2:
            viewports.append(entity)
        else:
            native.append(entity)
    for vp in viewports:
        mapper = _viewport_mapper(vp)
        if mapper is None:
            continue
        to_paper, clip = mapper
        for prim in _collect_primitives(doc.modelspace()):  # type: ignore[union-attr]
            mapped = _map_primitive(prim, to_paper, clip)
            if mapped is not None:
                out.append(mapped)
    out.extend(_collect_primitives(native))
    return out


def _viewport_mapper(vp: object):
    try:
        dxf = vp.dxf  # type: ignore[attr-defined]
        cx, cy = float(dxf.center.x), float(dxf.center.y)
        width, height = float(dxf.width), float(dxf.height)
        view_h = float(dxf.view_height)
        vc = dxf.view_center_point
        vx, vy = float(vc.x), float(vc.y)
    except Exception:  # noqa: BLE001
        return None
    if height < 1 or view_h < 1:
        return None
    scale = height / view_h
    x0, x1 = cx - width / 2.0, cx + width / 2.0
    y0, y1 = cy - height / 2.0, cy + height / 2.0
    pad = 0.8

    def to_paper(x: float, y: float) -> tuple[float, float]:
        return (cx + (x - vx) * scale, cy + (y - vy) * scale)

    def clip(px: float, py: float) -> bool:
        return (x0 - pad) <= px <= (x1 + pad) and (y0 - pad) <= py <= (y1 + pad)

    return to_paper, clip


def _map_primitive(prim: tuple, to_paper, clip) -> tuple | None:
    kind = prim[0]
    color = prim[-1]
    if kind == "line":
        a = to_paper(*prim[1])
        b = to_paper(*prim[2])
        if not (clip(*a) or clip(*b)):
            return None
        return ("line", a, b, color)
    if kind == "circle":
        c = to_paper(*prim[1])
        r = prim[2] * _viewport_scale(to_paper)
        if not clip(*c):
            return None
        return ("circle", c, r, color)
    if kind == "text":
        p = to_paper(*prim[1])
        if not clip(*p):
            return None
        return ("text", p, prim[2], color)
    if kind == "solid":
        pts = [to_paper(*p) for p in prim[1]]
        if not any(clip(*p) for p in pts):
            return None
        return ("solid", pts, color)
    return None


def _viewport_scale(to_paper) -> float:
    a = to_paper(0.0, 0.0)
    b = to_paper(1000.0, 0.0)
    return max(((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2) ** 0.5 / 1000.0, 1e-6)


def _collect_primitives(layout: object) -> list[tuple]:
    out: list[tuple] = []
    for entity in _iter_drawables(layout):
        try:
            out.extend(_entity_primitives(entity))
        except Exception:  # noqa: BLE001
            continue
    return out


def _entity_primitives(entity: object) -> list[tuple]:
    kind = entity.dxftype()  # type: ignore[attr-defined]
    color = _DIM_COLOR if str(getattr(entity.dxf, "layer", "") or "") == LAYER_DIM else (0, 0, 0)
    if kind == "LINE":
        return [
            (
                "line",
                (float(entity.dxf.start.x), float(entity.dxf.start.y)),
                (float(entity.dxf.end.x), float(entity.dxf.end.y)),
                color,
            )
        ]
    if kind == "LWPOLYLINE":
        pts = [(float(p[0]), float(p[1])) for p in entity]  # type: ignore[union-attr]
        if len(pts) < 2:
            return []
        seq = list(pts)
        if bool(entity.closed) and seq[0] != seq[-1]:  # type: ignore[union-attr]
            seq.append(seq[0])
        return [("line", a, b, color) for a, b in zip(seq, seq[1:], strict=False)]
    if kind == "POLYLINE":
        pts = [(float(v.dxf.location.x), float(v.dxf.location.y)) for v in entity.vertices]
        if len(pts) < 2:
            return []
        seq = list(pts)
        if bool(entity.is_closed) and seq[0] != seq[-1]:
            seq.append(seq[0])
        return [("line", a, b, color) for a, b in zip(seq, seq[1:], strict=False)]
    if kind in {"CIRCLE", "ARC"}:
        c = entity.dxf.center
        return [("circle", (float(c.x), float(c.y)), float(entity.dxf.radius), color)]
    if kind == "SOLID":
        pts = [(float(p.x), float(p.y)) for p in entity.wcs_vertices()]
        if len(pts) >= 3:
            return [("solid", pts, color)]
        return []
    if kind in {"TEXT", "MTEXT"}:
        ins = entity.dxf.insert
        raw = entity.plain_text() if kind == "MTEXT" else str(entity.dxf.text or "")
        blob = raw.strip()
        if not blob:
            return []
        if kind == "TEXT":
            return [("text", (float(ins.x), float(ins.y)), blob[:48], color)]
        height = float(getattr(entity.dxf, "char_height", 2.6) or 2.6)
        out: list[tuple] = []
        for i, line in enumerate([ln.strip() for ln in blob.splitlines() if ln.strip()][:8]):
            out.append(
                (
                    "text",
                    (float(ins.x), float(ins.y) - i * height * 1.65),
                    line[:160],
                    color,
                )
            )
        return out
    return []


def _iter_drawables(layout: object):
    entities = layout if not hasattr(layout, "dxftype") else [layout]
    for entity in entities:  # type: ignore[union-attr]
        try:
            kind = entity.dxftype()
        except Exception:  # noqa: BLE001
            continue
        if kind in {"INSERT", "DIMENSION"}:
            try:
                yield from _iter_drawables(entity.virtual_entities())
            except Exception:  # noqa: BLE001
                continue
            continue
        if kind == "VIEWPORT":
            continue
        if str(getattr(entity.dxf, "layer", "") or "") == LAYER_META:
            continue
        yield entity


def _paint(primitives: list[tuple], *, max_px: int) -> bytes:
    xs: list[float] = []
    ys: list[float] = []
    for prim in primitives:
        kind = prim[0]
        if kind == "line":
            xs.extend([prim[1][0], prim[2][0]])
            ys.extend([prim[1][1], prim[2][1]])
        elif kind == "circle":
            x, y = prim[1]
            r = prim[2]
            xs.extend([x - r, x + r])
            ys.extend([y - r, y + r])
        elif kind == "text":
            xs.append(prim[1][0])
            ys.append(prim[1][1])
        elif kind == "solid":
            xs.extend(p[0] for p in prim[1])
            ys.extend(p[1] for p in prim[1])
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
    scale = max(0.002, min(scale, 28.0))
    img_w = int(2 * _PAD + span_x * scale)
    img_h = int(2 * _PAD + span_y * scale)
    image = Image.new("RGB", (max(64, img_w), max(64, img_h)), (255, 255, 255))
    draw = ImageDraw.Draw(image)

    def xy(x: float, y: float) -> tuple[float, float]:
        return (_PAD + (x - min_x) * scale, _PAD + (max_y - y) * scale)

    font = _font(max(11, min(18, int(3.2 * scale) or 12)))
    for prim in primitives:
        kind = prim[0]
        if kind == "line":
            draw.line([xy(*prim[1]), xy(*prim[2])], fill=prim[3], width=1)
        elif kind == "circle":
            x, y = prim[1]
            r = prim[2]
            draw.ellipse([xy(x - r, y + r), xy(x + r, y - r)], outline=prim[3])
        elif kind == "solid":
            pts = [xy(*p) for p in prim[1]]
            if len(pts) >= 3:
                draw.polygon(pts, fill=prim[2], outline=prim[2])
        elif kind == "text":
            draw.text(xy(*prim[1]), prim[2], fill=prim[3], font=font)
    buf = BytesIO()
    image.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


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
