"""从布置几何生成尺寸链（米制）。不让 LLM 填尺寸。"""

from __future__ import annotations

import math
from dataclasses import dataclass

from api.services.layouts.pack import expand_stalls, rect_corners
from api.services.layouts.schema import (
    EvChargingStationPlan,
    site_boundary_m,
    site_is_irregular,
)


@dataclass(frozen=True)
class DimSpec:
    p1: tuple[float, float]
    p2: tuple[float, float]
    offset_m: float


def build_sheet_dimensions(plan: EvChargingStationPlan) -> list[DimSpec]:
    dims: list[DimSpec] = []
    dims.extend(_site_edge_dims(plan))
    dims.extend(_stall_dims(plan))
    dims.extend(_aisle_dims(plan))
    dims.extend(_equipment_box_dims(plan))
    return _dedupe(dims)


def _site_edge_dims(plan: EvChargingStationPlan) -> list[DimSpec]:
    poly = site_boundary_m(plan.site)
    if len(poly) < 2:
        w, h = float(plan.site.widthM), float(plan.site.heightM)
        poly = [(0.0, 0.0), (w, 0.0), (w, h), (0.0, h), (0.0, 0.0)]
    if poly[0] == poly[-1] and len(poly) > 1:
        ring = poly[:-1]
    else:
        ring = poly
    if len(ring) < 2:
        return []
    cx = sum(p[0] for p in ring) / len(ring)
    cy = sum(p[1] for p in ring) / len(ring)
    offset = 3.6 if site_is_irregular(plan.site) else 2.8
    out: list[DimSpec] = []
    n = len(ring)
    for i in range(n):
        a, b = ring[i], ring[(i + 1) % n]
        if math.hypot(b[0] - a[0], b[1] - a[1]) < 1.0:
            continue
        out.append(DimSpec(a, b, _outward_offset(a, b, (cx, cy), offset)))
    return out


def _stall_dims(plan: EvChargingStationPlan) -> list[DimSpec]:
    row = next((r for r in plan.parkingRows if float(r.stallLengthM) >= 10), None)
    if row is None and plan.parkingRows:
        row = plan.parkingRows[0]
    if row is None:
        return []
    stalls = expand_stalls(row)
    if not stalls:
        return []
    rect, _ = stalls[0]
    pts = rect_corners(rect)
    if len(pts) < 4:
        return []
    width = DimSpec(pts[0], pts[1], _outward_offset(pts[0], pts[1], _rect_center(pts), 1.6))
    depth = DimSpec(pts[1], pts[2], _outward_offset(pts[1], pts[2], _rect_center(pts), 1.6))
    return [width, depth]


def _aisle_dims(plan: EvChargingStationPlan) -> list[DimSpec]:
    for aisle in plan.aisles:
        line = [(p.x, p.y) for p in aisle.centerline]
        if len(line) < 2:
            continue
        i = max(1, len(line) // 2)
        a, b = line[i - 1], line[i]
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / length, dx / length
        mx, my = (a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0
        half = float(aisle.widthM) / 2.0
        p1 = (mx + nx * half, my + ny * half)
        p2 = (mx - nx * half, my - ny * half)
        return [DimSpec(p1, p2, 2.2)]
    return []


def _equipment_box_dims(plan: EvChargingStationPlan) -> list[DimSpec]:
    txs = [eq for eq in plan.equipment if eq.type in {"box_transformer", "ring_box_transformer"}]
    if len(txs) < 1:
        return []
    xs = [eq.x for eq in txs]
    ys = [eq.y for eq in txs]
    pad = 2.4
    x0, x1 = min(xs) - pad, max(xs) + pad
    y0, y1 = min(ys) - pad, max(ys) + pad
    if x1 - x0 < 1.0 or y1 - y0 < 1.0:
        return []
    return [
        DimSpec((x0, y1), (x1, y1), 1.8),
        DimSpec((x0, y0), (x0, y1), -1.8),
    ]


def _rect_center(pts: list[tuple[float, float]]) -> tuple[float, float]:
    return sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)


def _outward_offset(
    p1: tuple[float, float],
    p2: tuple[float, float],
    inside: tuple[float, float],
    mag: float,
) -> float:
    """ezdxf aligned dim：正 distance 在 p1→p2 左侧。"""
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    left = (-dy, dx)
    mid = ((p1[0] + p2[0]) / 2.0, (p1[1] + p2[1]) / 2.0)
    to_inside = (inside[0] - mid[0], inside[1] - mid[1])
    if left[0] * to_inside[0] + left[1] * to_inside[1] > 0:
        return -abs(mag)
    return abs(mag)


def _dedupe(dims: list[DimSpec]) -> list[DimSpec]:
    seen: set[tuple[float, float, float, float, float]] = set()
    out: list[DimSpec] = []
    for dim in dims:
        key = (
            round(dim.p1[0], 2),
            round(dim.p1[1], 2),
            round(dim.p2[0], 2),
            round(dim.p2[1], 2),
            round(dim.offset_m, 2),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(dim)
    return out
