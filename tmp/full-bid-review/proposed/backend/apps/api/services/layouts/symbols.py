"""平面图素材：轿车/重卡俯视线稿、过道马路虚线。单位米。"""

from __future__ import annotations

import math

from api.services.layouts.schema import AisleSpec, PointM, RectM

_CAR_LEN, _CAR_WID = 4.55, 1.78
_TRUCK_LEN, _TRUCK_WID = 14.8, 2.55


def _rot(px: float, py: float, ox: float, oy: float, rad: float) -> tuple[float, float]:
    dx, dy = px - ox, py - oy
    c, s = math.cos(rad), math.sin(rad)
    return ox + dx * c - dy * s, oy + dx * s + dy * c


def _vehicle_frame(
    rect: RectM, side: str
) -> tuple[float, float, tuple[float, float], tuple[float, float], float, float]:
    """车位中心 + 指向车头的 along、横向 across、可用长宽。"""
    cx = rect.x + rect.w / 2
    cy = rect.y + rect.h / 2
    s = (side or "head").lower()
    if s == "left":
        along, across, length, width = (-1.0, 0.0), (0.0, 1.0), rect.w, rect.h
    elif s == "right":
        along, across, length, width = (1.0, 0.0), (0.0, -1.0), rect.w, rect.h
    elif s == "tail":
        along, across, length, width = (0.0, -1.0), (-1.0, 0.0), rect.h, rect.w
    else:
        along, across, length, width = (0.0, 1.0), (1.0, 0.0), rect.h, rect.w
    return cx, cy, along, across, float(length), float(width)


def _map_pts(
    rect: RectM,
    along: tuple[float, float],
    across: tuple[float, float],
    cx: float,
    cy: float,
    local: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    rad = math.radians(rect.angleDeg)
    out: list[tuple[float, float]] = []
    for a, c in local:
        x = cx + along[0] * a + across[0] * c
        y = cy + along[1] * a + across[1] * c
        if abs(rad) > 1e-6:
            x, y = _rot(x, y, rect.x, rect.y, rad)
        out.append((x, y))
    return out


def _arc_pts(
    cx: float, cy: float, r: float, a0: float, a1: float, n: int = 8
) -> list[tuple[float, float]]:
    return [
        (cx + r * math.cos(a0 + (a1 - a0) * i / n), cy + r * math.sin(a0 + (a1 - a0) * i / n))
        for i in range(n + 1)
    ]


def _local_ellipse(
    cx: float, cy: float, hx: float, hy: float, n: int = 10
) -> list[tuple[float, float]]:
    return [
        (cx + hx * math.cos(2 * math.pi * i / n), cy + hy * math.sin(2 * math.pi * i / n))
        for i in range(n)
    ]


def _sedan_body(length: float, width: float) -> list[tuple[float, float]]:
    """圆角矩形车身：头尾钝口，两侧近乎平行，前脸略收。"""
    l, w = length / 2.0, width / 2.0
    w_tail, w_nose = w * 0.98, w * 0.93
    r_tail, r_nose = w * 0.26, w * 0.32
    pts: list[tuple[float, float]] = []
    pts.extend(_arc_pts(-l + r_tail, -w_tail + r_tail, r_tail, math.pi, 1.5 * math.pi, 6))
    pts.extend(_arc_pts(l - r_nose, -w_nose + r_nose, r_nose, 1.5 * math.pi, 2 * math.pi, 7))
    pts.extend(_arc_pts(l - r_nose, w_nose - r_nose, r_nose, 0.0, 0.5 * math.pi, 7))
    pts.extend(_arc_pts(-l + r_tail, w_tail - r_tail, r_tail, 0.5 * math.pi, math.pi, 6))
    return pts


def _car_locals(length: float, width: float) -> list[list[tuple[float, float]]]:
    """CAD 小轿车俯视：圆角车身、梯形风挡与后窗、轮子、后视镜。"""
    l, w = length / 2.0, width / 2.0
    body = _sedan_body(length, width)
    windshield = [
        (l * 0.10, -w * 0.58),
        (l * 0.10, w * 0.58),
        (l * 0.38, w * 0.30),
        (l * 0.44, w * 0.16),
        (l * 0.44, -w * 0.16),
        (l * 0.38, -w * 0.30),
    ]
    rear = [
        (-l * 0.48, -w * 0.24),
        (-l * 0.48, w * 0.24),
        (-l * 0.16, w * 0.54),
        (-l * 0.16, -w * 0.54),
    ]
    mw, md = w * 0.15, l * 0.04
    ax = l * 0.12
    left_mirror = [
        (ax - md, w * 0.93),
        (ax + md, w * 0.93),
        (ax + md * 0.25, w * 0.93 + mw),
        (ax - md * 0.15, w * 0.93 + mw),
    ]
    right_mirror = [
        (ax - md, -w * 0.93),
        (ax + md, -w * 0.93),
        (ax + md * 0.25, -w * 0.93 - mw),
        (ax - md * 0.15, -w * 0.93 - mw),
    ]
    wheels = [
        _local_ellipse(-l * 0.50, w * 0.91, l * 0.14, w * 0.14, 12),
        _local_ellipse(-l * 0.50, -w * 0.91, l * 0.14, w * 0.14, 12),
        _local_ellipse(l * 0.32, w * 0.89, l * 0.14, w * 0.14, 12),
        _local_ellipse(l * 0.32, -w * 0.89, l * 0.14, w * 0.14, 12),
    ]
    return [body, windshield, rear, left_mirror, right_mirror, *wheels]


def _truck_locals(length: float, width: float) -> list[list[tuple[float, float]]]:
    l, w = length / 2, width / 2
    cab = [
        (l * 0.42, -w * 0.78),
        (l * 0.72, -w * 0.70),
        (l * 0.88, -w * 0.42),
        (l * 0.94, 0.0),
        (l * 0.88, w * 0.42),
        (l * 0.72, w * 0.70),
        (l * 0.42, w * 0.78),
        (l * 0.36, w * 0.62),
        (l * 0.36, -w * 0.62),
    ]
    windshield = [
        (l * 0.58, -w * 0.48),
        (l * 0.58, w * 0.48),
        (l * 0.78, w * 0.32),
        (l * 0.78, -w * 0.32),
    ]
    chassis = [
        (-l * 0.96, -w * 0.62),
        (-l * 0.96, w * 0.62),
        (l * 0.36, w * 0.62),
        (l * 0.36, -w * 0.62),
    ]
    bunk = [
        (l * 0.10, -w * 0.70),
        (l * 0.10, w * 0.70),
        (l * 0.36, w * 0.70),
        (l * 0.36, -w * 0.70),
    ]
    return [chassis, bunk, cab, windshield]


def _bolt_locals(ax: float, cx: float, scale: float) -> list[tuple[float, float]]:
    s = scale
    return [
        (ax + 0.12 * s, cx),
        (ax + 0.02 * s, cx + 0.10 * s),
        (ax + 0.06 * s, cx + 0.10 * s),
        (ax - 0.12 * s, cx + 0.02 * s),
        (ax - 0.02 * s, cx - 0.10 * s),
        (ax - 0.06 * s, cx - 0.10 * s),
        (ax + 0.12 * s, cx),
    ]


def vehicle_polylines(
    rect: RectM, charger_side: str, *, truck: bool
) -> list[list[tuple[float, float]]]:
    """车位内俯视车辆闭合折线，车头朝向充电桩。"""
    cx, cy, along, across, stall_l, stall_w = _vehicle_frame(rect, charger_side)
    if truck:
        length = min(stall_l * 0.86, _TRUCK_LEN)
        width = min(stall_w * 0.72, _TRUCK_WID)
        locals_ = _truck_locals(length, width)
        bolts = [
            _bolt_locals(length * 0.22, width * 0.42, 0.55),
            _bolt_locals(length * 0.22, -width * 0.42, 0.55),
        ]
        locals_.extend(bolts)
    else:
        length = min(stall_l * 0.78, _CAR_LEN)
        width = min(stall_w * 0.62, _CAR_WID)
        locals_ = _car_locals(length, width)
    return [_map_pts(rect, along, across, cx, cy, ring) for ring in locals_]


def _unit(dx: float, dy: float) -> tuple[float, float]:
    n = math.hypot(dx, dy) or 1.0
    return dx / n, dy / n


def _offset_polyline(pts: list[tuple[float, float]], dist: float) -> list[tuple[float, float]]:
    if len(pts) < 2:
        return list(pts)
    out: list[tuple[float, float]] = []
    for i, (x, y) in enumerate(pts):
        if i == 0:
            tx, ty = _unit(pts[1][0] - x, pts[1][1] - y)
        elif i == len(pts) - 1:
            tx, ty = _unit(x - pts[i - 1][0], y - pts[i - 1][1])
        else:
            t1 = _unit(x - pts[i - 1][0], y - pts[i - 1][1])
            t2 = _unit(pts[i + 1][0] - x, pts[i + 1][1] - y)
            tx, ty = _unit(t1[0] + t2[0], t1[1] + t2[1])
        nx, ny = -ty, tx
        out.append((x + nx * dist, y + ny * dist))
    return out


def _sample_centerline(points: list[PointM], step: float = 0.45) -> list[tuple[float, float]]:
    raw = [(float(p.x), float(p.y)) for p in points]
    if len(raw) < 2:
        return raw
    out = [raw[0]]
    for a, b in zip(raw, raw[1:]):
        seg = math.hypot(b[0] - a[0], b[1] - a[1])
        n = max(1, int(seg / step))
        for i in range(1, n + 1):
            t = i / n
            out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
    return out


def aisle_marking_polylines(aisle: AisleSpec) -> list[list[tuple[float, float]]]:
    """过道只画沿中心线的平行虚线，不在两端加半圆包裹。"""
    center = _sample_centerline(list(aisle.centerline), step=0.5)
    if len(center) < 2:
        return []
    half = min(max(1.2, float(aisle.widthM) / 2.0), 3.6)
    lines = [_offset_polyline(center, d) for d in (-half, 0.0, half)]
    return [ln for ln in lines if len(ln) >= 2]


def dash_segments(
    pts: list[tuple[float, float]], dash: float = 0.9, gap: float = 0.55
) -> list[list[tuple[float, float]]]:
    """把折线切成虚线段。"""
    if len(pts) < 2:
        return []
    segs: list[list[tuple[float, float]]] = []
    carry = 0.0
    drawing = True
    period = dash + gap
    for a, b in zip(pts, pts[1:]):
        dx, dy = b[0] - a[0], b[1] - a[1]
        seg_len = math.hypot(dx, dy)
        if seg_len < 1e-6:
            continue
        ux, uy = dx / seg_len, dy / seg_len
        pos = 0.0
        while pos < seg_len - 1e-6:
            remain = (dash if drawing else gap) - carry
            take = min(remain, seg_len - pos)
            if drawing:
                p0 = (a[0] + ux * pos, a[1] + uy * pos)
                p1 = (a[0] + ux * (pos + take), a[1] + uy * (pos + take))
                segs.append([p0, p1])
            pos += take
            carry += take
            if carry + 1e-9 >= (dash if drawing else gap):
                carry = 0.0
                drawing = not drawing
    return segs

