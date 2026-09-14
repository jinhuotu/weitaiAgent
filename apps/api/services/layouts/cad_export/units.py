"""内部米制 → CAD 毫米。"""

from __future__ import annotations

import math
from collections.abc import Iterable

from api.services.layouts.schema import PointM, SurveyFrame

MM_PER_M = 1000.0
INSUNITS_MM = 4

PAPER_MM: dict[str, tuple[float, float]] = {
    "A3": (420.0, 297.0),
    "A2": (594.0, 420.0),
    "A1": (841.0, 594.0),
}

SCALE_STEPS = (50, 100, 150, 200, 250, 300, 400, 500, 800, 1000)
PAPER_RANK = ("A3", "A2", "A1")


def mm(meters: float) -> float:
    return float(meters) * MM_PER_M


def mm_pt(x: float, y: float) -> tuple[float, float]:
    return mm(x), mm(y)


def mm_pair(pt: PointM | tuple[float, float]) -> tuple[float, float]:
    if isinstance(pt, tuple):
        return mm_pt(pt[0], pt[1])
    return mm_pt(pt.x, pt.y)


def mm_pts(pts: Iterable[PointM | tuple[float, float]]) -> list[tuple[float, float]]:
    return [mm_pair(p) for p in pts]


def parse_scale(raw: str | None) -> int:
    text = (raw or "1:200").replace("：", ":").strip()
    if ":" in text:
        text = text.split(":")[-1]
    try:
        value = int(round(float(text)))
    except (TypeError, ValueError):
        return 200
    return value if value > 0 else 200


def snap_scale(needed: float, preferred: int) -> int:
    want = max(float(needed), float(preferred))
    for step in SCALE_STEPS:
        if step + 1e-6 >= want:
            return int(step)
    return int(max(SCALE_STEPS[-1], round(want / 50.0) * 50.0))


def overlay_viewport(pw: float, ph: float) -> tuple[float, float, float, float]:
    """图框压在视口上，视口几乎铺满内框，才能在 A3 上保住 1:200。"""
    return 16.0, 16.0, pw - 16.0, ph - 16.0


def choose_paper_and_scale(
    paper: str,
    scale: str | None,
    extents: tuple[float, float, float, float],
) -> tuple[str, int]:
    preferred = parse_scale(scale)
    start = paper if paper in PAPER_RANK else "A3"
    order = list(PAPER_RANK[PAPER_RANK.index(start) :])
    min_x, min_y, max_x, max_y = extents
    need_w = max((max_x - min_x) * MM_PER_M, 1.0)
    need_h = max((max_y - min_y) * MM_PER_M, 1.0)
    for size in order:
        pw, ph = PAPER_MM[size]
        x0, y0, x1, y1 = overlay_viewport(pw, ph)
        vp_w, vp_h = max(x1 - x0, 1.0), max(y1 - y0, 1.0)
        if need_w <= vp_w * preferred * 1.02 and need_h <= vp_h * preferred * 1.02:
            return size, preferred
    size = order[-1]
    pw, ph = PAPER_MM[size]
    x0, y0, x1, y1 = overlay_viewport(pw, ph)
    vp_w, vp_h = max(x1 - x0, 1.0), max(y1 - y0, 1.0)
    needed = max(need_w / vp_w, need_h / vp_h)
    return size, snap_scale(needed, preferred)


def survey_is_named(survey: SurveyFrame | None) -> bool:
    return bool(survey is not None and str(survey.name or "").strip())


def survey_needs_transform(survey: SurveyFrame | None) -> bool:
    if survey is None:
        return False
    return (
        abs(float(survey.originXm or 0.0)) > 1e-9
        or abs(float(survey.originYm or 0.0)) > 1e-9
        or abs(float(survey.rotationDeg or 0.0)) > 1e-9
    )


def local_to_survey_m(
    survey: SurveyFrame | None, x: float, y: float
) -> tuple[float, float]:
    if survey is None:
        return float(x), float(y)
    rad = math.radians(float(survey.rotationDeg or 0.0))
    c, s = math.cos(rad), math.sin(rad)
    ox, oy = float(survey.originXm or 0.0), float(survey.originYm or 0.0)
    return ox + x * c - y * s, oy + x * s + y * c


def survey_to_local_m(
    survey: SurveyFrame | None, x: float, y: float
) -> tuple[float, float]:
    if survey is None:
        return float(x), float(y)
    rad = math.radians(float(survey.rotationDeg or 0.0))
    c, s = math.cos(rad), math.sin(rad)
    ox, oy = float(survey.originXm or 0.0), float(survey.originYm or 0.0)
    dx, dy = float(x) - ox, float(y) - oy
    return dx * c + dy * s, -dx * s + dy * c
