"""对话草稿：PDF/DXF/DWG 栅格化给读图，CAD 几何回读已有车位。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from io import BytesIO, StringIO
from typing import Any

from api.services.layouts.cad_export.layers import LAYER_META, LAYER_PARK, LAYER_SHEET, LAYER_SITE
from api.services.layouts.cad_export.units import MM_PER_M
from api.services.layouts.parse import parse_plan
from api.services.layouts.schema import (
    ChargerOnRow,
    EvChargingStationPlan,
    ParkingRowSpec,
    PointM,
)
from common.errors import AppError, ErrorCode

_RELAYOUT_RE = re.compile(r"重排|重新排列|不要照着|别照着|按规范(?:重新)?(?:布置|排列)")
_CAR_W, _CAR_L = (2.2, 3.8), (5.0, 7.8)
_TRUCK_W, _TRUCK_L = (4.2, 6.8), (13.0, 20.5)

DRAFT_MIME = {
    "application/pdf": "pdf",
    "application/dxf": "dxf",
    "application/x-dxf": "dxf",
    "image/vnd.dxf": "dxf",
    "application/acad": "dwg",
    "application/x-acad": "dwg",
    "application/dwg": "dwg",
    "application/x-dwg": "dwg",
    "image/vnd.dwg": "dwg",
    "application/octet-stream": "bin",
}

MAX_DRAFT_BYTES = 12 * 1024 * 1024


@dataclass
class ChatUploads:
    images: list[tuple[str, bytes]]
    draft_plan: dict[str, Any] | None = None


def guess_draft_kind(mime: str, filename: str, blob: bytes) -> str | None:
    name = (filename or "").lower()
    mime = (mime or "").strip().lower()
    if blob[:4] == b"%PDF" or mime == "application/pdf" or name.endswith(".pdf"):
        return "pdf"
    if blob[:4] == b"AC10" or name.endswith(".dwg") or "dwg" in mime or "acad" in mime:
        return "dwg"
    head = blob[:120].lstrip().upper()
    if name.endswith(".dxf") or "dxf" in mime or (
        head.startswith(b"0") and b"SECTION" in blob[:400].upper()
    ):
        return "dxf"
    return None


def should_trace_draft(query: str, has_rows: bool) -> bool:
    if not has_rows:
        return False
    return not bool(_RELAYOUT_RE.search(query or ""))


def fill_fleet_from_draft(
    cons: Any,
    draft: EvChargingStationPlan | None,
    vis_rows: list[ParkingRowSpec] | None = None,
) -> Any:
    rows = list((draft.parkingRows if draft is not None else None) or vis_rows or [])
    if not rows:
        return cons
    cars = sum(int(r.stalls) for r in rows if float(r.stallLengthM) < 10)
    trucks = sum(int(r.stalls) for r in rows if float(r.stallLengthM) >= 10)
    out = cons.model_copy(deep=True)
    if out.fleet.cars is None and out.fleet.piles is None:
        out.fleet.cars = cars or None
        if out.fleet.trucks is None:
            out.fleet.trucks = trucks or 0
        piles = int(out.fleet.cars or 0) + int(out.fleet.trucks or 0)
        out.fleet.piles = piles or None
    elif out.fleet.trucks is None and trucks:
        out.fleet.trucks = trucks
    return out


def overlay_draft_on_plan(
    plan: EvChargingStationPlan,
    draft: EvChargingStationPlan | None,
    vis_rows: list[ParkingRowSpec] | None = None,
) -> EvChargingStationPlan:
    out = plan.model_copy(deep=True)
    rows = list((draft.parkingRows if draft is not None else None) or vis_rows or [])
    if rows:
        out.parkingRows = [r.model_copy(deep=True) for r in rows]
    if draft is not None and len(draft.site.polygon or []) >= 3:
        out.site.polygon = [PointM(x=p.x, y=p.y) for p in draft.site.polygon]
        out.site.widthM = float(draft.site.widthM)
        out.site.heightM = float(draft.site.heightM)
        if draft.site.gate is not None and out.site.gate is None:
            out.site.gate = draft.site.gate.model_copy(deep=True)
    return out


def parse_parking_rows_from_text(*texts: str) -> list[ParkingRowSpec]:
    blob = "\n".join(t for t in texts if t)
    if not blob or "parkingRows" not in blob:
        return []
    data = _json_array_after(blob, "parkingRows")
    if not isinstance(data, list) or not data:
        return []
    try:
        plan = parse_plan(
            {
                "kind": "ev_charging_station_plan",
                "site": {"widthM": 40, "heightM": 40},
                "parkingRows": data,
            }
        )
    except Exception:  # noqa: BLE001
        return []
    return list(plan.parkingRows)


def materialize_uploads(items: list[tuple[str, bytes, str]]) -> ChatUploads:
    images: list[tuple[str, bytes]] = []
    draft: dict[str, Any] | None = None
    for mime, blob, name in items:
        kind = guess_draft_kind(mime, name, blob)
        if kind is None:
            images.append(("image/jpeg" if mime == "image/jpg" else mime, blob))
            continue
        if kind == "pdf":
            images.append(("image/jpeg", rasterize_pdf_bytes(blob)))
            continue
        dxf = blob if kind == "dxf" else dwg_bytes_to_dxf(blob)
        plan = plan_from_cad_bytes(dxf)
        if plan is not None and plan.parkingRows and draft is None:
            draft = plan.model_dump(mode="json")
        images.append(("image/png", rasterize_dxf_bytes(dxf)))
    return ChatUploads(images=images, draft_plan=draft)


def rasterize_pdf_bytes(raw: bytes, *, max_px: int = 1600) -> bytes:
    import pypdfium2 as pdfium

    if not raw:
        raise AppError(ErrorCode.VALIDATION, "PDF 草稿为空", status_code=422)
    try:
        doc = pdfium.PdfDocument(raw)
    except Exception as exc:  # noqa: BLE001
        raise AppError(ErrorCode.VALIDATION, "PDF 草稿无法读取", status_code=422) from exc
    try:
        if len(doc) < 1:
            raise AppError(ErrorCode.VALIDATION, "PDF 草稿没有页面", status_code=422)
        page = doc[0]
        w, h = page.get_size()
        longest = max(float(w), float(h), 1.0)
        scale = min(2.2, max(0.8, max_px / longest))
        bitmap = page.render(scale=scale)
        pil = bitmap.to_pil().convert("RGB")
        buf = BytesIO()
        pil.save(buf, format="JPEG", quality=85, optimize=True)
        return buf.getvalue()
    finally:
        doc.close()


def rasterize_dxf_bytes(raw: bytes, *, max_px: int = 1600) -> bytes:
    from api.services.layouts.cad import rasterize_doc

    try:
        doc = load_dxf_doc(raw)
    except Exception as exc:  # noqa: BLE001
        raise AppError(ErrorCode.VALIDATION, "DXF 草稿无法读取", status_code=422) from exc
    try:
        return rasterize_doc(doc, max_px=max_px)
    except Exception as exc:  # noqa: BLE001
        raise AppError(ErrorCode.VALIDATION, "DXF 草稿无法转成预览图", status_code=422) from exc


def dwg_bytes_to_dxf(raw: bytes) -> bytes:
    from api.services.layouts.cad_export.dwg import dwg_to_dxf_bytes

    if not raw:
        raise AppError(ErrorCode.VALIDATION, "DWG 草稿为空", status_code=422)
    out = dwg_to_dxf_bytes(raw)
    if not out:
        raise AppError(
            ErrorCode.VALIDATION,
            "本机未安装 ODA 转换器，DWG 草稿无法读取，请另存为 DXF 或导出 PDF/PNG",
            status_code=422,
        )
    return out


def plan_from_cad_bytes(raw: bytes) -> EvChargingStationPlan | None:
    from api.services.layouts.cad_export.reader import plan_from_dxf_doc

    try:
        doc = load_dxf_doc(raw)
    except Exception:  # noqa: BLE001
        return None
    try:
        return plan_from_dxf_doc(doc)
    except Exception:  # noqa: BLE001
        pass
    try:
        return _plan_from_dxf_geometry(doc)
    except Exception:  # noqa: BLE001
        return None


def load_dxf_doc(raw: bytes) -> Any:
    import ezdxf

    if not raw:
        raise ValueError("empty dxf")
    if raw[:4] == b"AC10":
        raise ValueError("dwg bytes")
    try:
        return ezdxf.read(StringIO(raw.decode("utf-8-sig", errors="replace")))
    except Exception:  # noqa: BLE001
        return ezdxf.read(BytesIO(raw))


def _plan_from_dxf_geometry(doc: Any) -> EvChargingStationPlan | None:
    stalls, poly = _stalls_and_site(doc)
    if not stalls and len(poly) < 3:
        return None
    if poly:
        xs, ys = [p[0] for p in poly], [p[1] for p in poly]
        width = max(8.0, max(xs) - min(xs))
        height = max(8.0, max(ys) - min(ys))
        polygon = [PointM(x=x, y=y) for x, y in poly]
    else:
        xs = [s.origin.x for s in stalls] + [s.origin.x + 6 for s in stalls]
        ys = [s.origin.y for s in stalls] + [s.origin.y + 6 for s in stalls]
        width = max(8.0, max(xs) - min(xs) + 4)
        height = max(8.0, max(ys) - min(ys) + 4)
        polygon = []
    return parse_plan(
        {
            "kind": "ev_charging_station_plan",
            "site": {
                "widthM": min(500.0, width),
                "heightM": min(500.0, height),
                "polygon": [{"x": p.x, "y": p.y} for p in polygon],
            },
            "parkingRows": [r.model_dump(mode="json") for r in stalls],
        }
    )


def _stalls_and_site(doc: Any) -> tuple[list[ParkingRowSpec], list[tuple[float, float]]]:
    msp = doc.modelspace()
    boxes: list[tuple[float, float, float, float, bool]] = []
    site_pts: list[list[tuple[float, float]]] = []
    for entity in msp.query("LWPOLYLINE"):
        layer = str(getattr(entity.dxf, "layer", "") or "")
        if layer in {LAYER_META, LAYER_SHEET}:
            continue
        pts = [(float(p[0]), float(p[1])) for p in entity]
        closed = bool(getattr(entity, "closed", False))
        if layer == LAYER_SITE and len(pts) >= 3:
            site_pts.append(pts)
            continue
        if not closed or len(pts) < 4:
            continue
        box = _aabb_if_rect(pts)
        if box is None:
            continue
        boxes.append((*box, layer == LAYER_PARK))
    nums = [c for b in boxes for c in b[:4]] + [c for poly in site_pts for p in poly for c in p]
    scale = _unit_scale(nums)
    boxes = [(x * scale, y * scale, w * scale, h * scale, park) for x, y, w, h, park in boxes]
    site_pts = [[(x * scale, y * scale) for x, y in poly] for poly in site_pts]
    min_x, min_y = _origin_shift([(b[0], b[1], b[2], b[3]) for b in boxes], site_pts)
    boxes = [(x - min_x, y - min_y, w, h, park) for x, y, w, h, park in boxes]
    site_pts = [[(x - min_x, y - min_y) for x, y in poly] for poly in site_pts]
    classified: list[tuple[float, float, float, float, bool, bool]] = []
    for b in boxes:
        hit = _classify_box(b[:4])
        if hit is None:
            continue
        classified.append((hit[0], hit[1], hit[2], hit[3], b[4], hit[5]))
    prefer_park = [s for s in classified if s[4]]
    use = prefer_park or classified
    rows = _cluster_stalls([(s[0], s[1], s[2], s[3], s[5]) for s in use])
    poly = max(site_pts, key=len) if site_pts else []
    if poly and poly[0] == poly[-1] and len(poly) > 3:
        poly = poly[:-1]
    return rows, poly


def _aabb_if_rect(pts: list[tuple[float, float]]) -> tuple[float, float, float, float] | None:
    uniq: list[tuple[float, float]] = []
    for p in pts:
        if not uniq or abs(p[0] - uniq[-1][0]) > 0.05 or abs(p[1] - uniq[-1][1]) > 0.05:
            uniq.append(p)
    if uniq and abs(uniq[0][0] - uniq[-1][0]) < 0.05 and abs(uniq[0][1] - uniq[-1][1]) < 0.05:
        uniq = uniq[:-1]
    if len(uniq) != 4:
        return None
    xs, ys = [p[0] for p in uniq], [p[1] for p in uniq]
    w, h = max(xs) - min(xs), max(ys) - min(ys)
    if w < 800 and h < 800 and max(w, h) < 40:
        # already meters? keep
        pass
    if w < 1.5 or h < 1.5:
        return None
    # 四边近似轴对齐
    for a, b in zip(uniq, uniq[1:] + uniq[:1], strict=False):
        dx, dy = abs(a[0] - b[0]), abs(a[1] - b[1])
        if dx > 0.15 * max(w, 1) and dy > 0.15 * max(h, 1):
            return None
    return min(xs), min(ys), w, h


def _unit_scale(coords: list[float]) -> float:
    if not coords:
        return 1.0 / MM_PER_M
    span = max(coords) - min(coords)
    if span > 400:
        return 1.0 / MM_PER_M
    return 1.0


def _origin_shift(
    boxes: list[tuple[float, float, float, float]],
    site_pts: list[list[tuple[float, float]]],
) -> tuple[float, float]:
    xs = [b[0] for b in boxes] + [p[0] for poly in site_pts for p in poly]
    ys = [b[1] for b in boxes] + [p[1] for poly in site_pts for p in poly]
    if not xs or not ys:
        return 0.0, 0.0
    return min(xs), min(ys)


def _classify_box(
    box: tuple[float, float, float, float],
) -> tuple[float, float, float, float, bool, bool] | None:
    x, y, w, h = box
    short, long = (w, h) if w <= h else (h, w)
    park_layer = False
    if _CAR_W[0] <= short <= _CAR_W[1] and _CAR_L[0] <= long <= _CAR_L[1]:
        return x, y, w, h, park_layer, False
    if _TRUCK_W[0] <= short <= _TRUCK_W[1] and _TRUCK_L[0] <= long <= _TRUCK_L[1]:
        return x, y, w, h, park_layer, True
    return None


def _cluster_stalls(
    stalls: list[tuple[float, float, float, float, bool]],
) -> list[ParkingRowSpec]:
    if not stalls:
        return []
    y_groups: dict[int, list[tuple[float, float, float, float, bool]]] = {}
    x_groups: dict[int, list[tuple[float, float, float, float, bool]]] = {}
    for s in stalls:
        cx, cy = s[0] + s[2] / 2.0, s[1] + s[3] / 2.0
        y_groups.setdefault(int(round(cy / 0.7)), []).append(s)
        x_groups.setdefault(int(round(cx / 0.7)), []).append(s)
    y_score = sum(len(g) for g in y_groups.values() if len(g) >= 2)
    x_score = sum(len(g) for g in x_groups.values() if len(g) >= 2)
    along = "x" if y_score >= x_score else "y"
    groups = y_groups if along == "x" else x_groups
    rows: list[ParkingRowSpec] = []
    start = 1
    for i, g in enumerate(groups.values(), start=1):
        g = sorted(g, key=lambda s: s[0] if along == "x" else s[1])
        sample = g[0]
        truck = sample[4]
        ox = min(s[0] for s in g)
        oy = min(s[1] for s in g)
        sw = 5.0 if truck else 3.0
        sl = 17.0 if truck else 6.0
        kind = "trucks" if truck else "cars"
        rows.append(
            ParkingRowSpec(
                id=f"{kind}_{i}",
                stalls=len(g),
                stallWidthM=sw,
                stallLengthM=sl,
                origin=PointM(x=ox, y=oy),
                along=along,  # type: ignore[arg-type]
                charger=ChargerOnRow(type="none", startNo=start, side="head"),
            )
        )
        start += len(g)
    return rows


def _json_array_after(blob: str, key: str) -> Any:
    m = re.search(rf"{re.escape(key)}\s*[:=]\s*\[", blob)
    if not m:
        return None
    i = blob.find("[", m.start())
    depth = 0
    in_str = False
    esc = False
    for j in range(i, len(blob)):
        ch = blob[j]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
            continue
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(blob[i : j + 1])
                except json.JSONDecodeError:
                    return None
    return None
