"""图纸空间：A3/A2 图框、图例、指北针、标题栏、设计说明。"""

from __future__ import annotations

import math
from typing import Any

from ezdxf.enums import MTextEntityAlignment, TextEntityAlignment

from api.services.layouts.cad_export.layers import (
    BLOCK_NORTH,
    LAYER_SHEET,
    STYLE_CN,
)
from api.services.layouts.cad_export.units import PAPER_MM, overlay_viewport
from api.services.layouts.schema import EvChargingStationPlan

_LEGEND_LABELS = {
    "ring_cabinet": "环网柜",
    "box_transformer": "箱式变电站",
    "group_host": "群冲主机柜",
    "dc_320kw": "320kW直流充电桩",
    "dc_160kw": "160kW直流充电桩",
    "dc_120kw": "120kW直流充电桩",
    "ac_14kw": "14kW交流充电桩",
    "parking": "充电车位",
    "truck": "重卡车",
    "greenery": "绿化",
    "tree": "树木",
    "fire_hydrant": "消火栓",
    "cable_well": "电缆井",
    "vent_grille": "通风格栅",
}


def paper_size(plan: EvChargingStationPlan, paper: str | None = None) -> tuple[float, float]:
    key = (paper or plan.sheetStyle.paper or "A3").upper()
    return PAPER_MM.get(key, PAPER_MM["A3"])


def draw_sheet(
    psp: Any,
    plan: EvChargingStationPlan,
    *,
    scale_n: int,
    paper: str | None = None,
    used_title_block: bool = False,
) -> tuple[float, float, float, float]:
    """画图框与表。视口让开标题栏和设计说明，返回图纸毫米。"""
    pw, ph = paper_size(plan, paper)
    style = plan.sheetStyle
    if not used_title_block:
        _frame(psp, pw, ph)
    tb_h = 0.0
    if style.showTitleBlock and not used_title_block:
        tb_h = _title_block(psp, plan, pw, ph, scale_n)
    notes_h = 0.0
    if plan.notes:
        notes_h = _notes(psp, plan, pw, ph, title_h=tb_h)
    if style.showLegend:
        _legend(psp, plan, ph)
    if style.showNorthArrow:
        _north(psp, pw, ph)
    bottom = 16.0 + max(tb_h, notes_h, 36.0) + 8.0
    return overlay_viewport(pw, ph, bottom=bottom)


def _frame(psp: Any, pw: float, ph: float) -> None:
    psp.add_lwpolyline(
        [(8, 8), (pw - 8, 8), (pw - 8, ph - 8), (8, ph - 8)],
        close=True,
        dxfattribs={"layer": LAYER_SHEET, "lineweight": 50},
    )
    psp.add_lwpolyline(
        [(12, 12), (pw - 12, 12), (pw - 12, ph - 12), (12, ph - 12)],
        close=True,
        dxfattribs={"layer": LAYER_SHEET, "lineweight": 25},
    )


def _title_block(
    psp: Any, plan: EvChargingStationPlan, pw: float, ph: float, scale_n: int
) -> float:
    tb = plan.titleBlock
    rows = [
        ("工程名称", (tb.project or tb.title or "充电站平面布置").strip()),
        ("图名", (tb.title or "充电站平面布置图").strip()),
        ("图号", (tb.sheetNo or "001").strip()),
        ("比例", f"1:{scale_n}"),
        ("阶段", (tb.stage or "施工图").strip()),
        ("设计", (tb.designer or "").strip()),
        ("审核", (tb.reviewer or "").strip()),
        ("制图", (tb.drawer or "").strip()),
        ("日期", (tb.date or "").strip()),
    ]
    if (tb.company or "").strip():
        rows.insert(0, ("单位", tb.company.strip()))
    row_h = 7.2
    key_w, val_w = 22.0, 78.0
    table_w = key_w + val_w
    table_h = row_h * len(rows)
    x1, y0 = pw - 14.0, 14.0
    x0, y1 = x1 - table_w, y0 + table_h
    psp.add_lwpolyline(
        [(x0, y0), (x1, y0), (x1, y1), (x0, y1)],
        close=True,
        dxfattribs={"layer": LAYER_SHEET},
    )
    psp.add_line((x0 + key_w, y0), (x0 + key_w, y1), dxfattribs={"layer": LAYER_SHEET})
    for i, (key, val) in enumerate(rows):
        y = y1 - i * row_h
        if i:
            psp.add_line((x0, y), (x1, y), dxfattribs={"layer": LAYER_SHEET})
        cy = y - row_h / 2.0
        _text(psp, key, (x0 + 1.6, cy), 2.4, align="LEFT")
        if val:
            _text(psp, val[:22], (x0 + key_w + 2.0, cy), 2.4, align="LEFT")
    return table_h


def _notes(
    psp: Any, plan: EvChargingStationPlan, pw: float, ph: float, *, title_h: float
) -> float:
    notes = [str(n).strip() for n in (plan.notes or []) if str(n).strip()]
    if not notes:
        return 0.0
    x0, y0 = 14.0, 14.0
    width = max(80.0, pw - 14.0 - 108.0 - 18.0)
    if title_h <= 0:
        width = pw - 28.0
    body = "设计说明\n" + "\n".join(notes[:6])
    n = 1 + len(notes[:6])
    height = max(28.0, 10.0 + 5.4 * n)
    if title_h > 0:
        height = max(height, title_h)
    psp.add_lwpolyline(
        [(x0, y0), (x0 + width, y0), (x0 + width, y0 + height), (x0, y0 + height)],
        close=True,
        dxfattribs={"layer": LAYER_SHEET},
    )
    mt = psp.add_mtext(
        body,
        dxfattribs={
            "layer": LAYER_SHEET,
            "style": STYLE_CN,
            "char_height": 2.6,
            "width": width - 4.0,
        },
    )
    mt.set_location((x0 + 2.0, y0 + height - 2.0), attachment_point=MTextEntityAlignment.TOP_LEFT)
    return height


def _legend(psp: Any, plan: EvChargingStationPlan, ph: float) -> float:
    items = [str(s) for s in (plan.legend or []) if s]
    if not items:
        return 0.0
    cols = 2 if len(items) > 3 else 1
    row_h, col_w = 8.0, 52.0
    rows = int(math.ceil(len(items) / cols))
    title_h = 7.0
    box_w = 8.0 + cols * col_w
    box_h = 6.0 + title_h + rows * row_h
    x0, y1 = 16.0, ph - 16.0
    y0 = y1 - box_h
    psp.add_lwpolyline(
        [(x0, y0), (x0 + box_w, y0), (x0 + box_w, y1), (x0, y1)],
        close=True,
        dxfattribs={"layer": LAYER_SHEET},
    )
    _text(psp, "图例", (x0 + box_w / 2.0, y1 - 3.6), 3.0, align="CENTER")
    psp.add_line((x0, y1 - title_h), (x0 + box_w, y1 - title_h), dxfattribs={"layer": LAYER_SHEET})
    if cols == 2:
        psp.add_line(
            (x0 + col_w + 4.0, y0),
            (x0 + col_w + 4.0, y1 - title_h),
            dxfattribs={"layer": LAYER_SHEET},
        )
    for i, sym in enumerate(items):
        r, c = i // cols, i % cols
        cx = x0 + 8.0 + c * col_w
        cy = y1 - title_h - 4.0 - r * row_h
        _legend_mark(psp, sym, cx, cy)
        _text(psp, _LEGEND_LABELS.get(sym, sym), (cx + 8.0, cy), 2.4, align="LEFT")
    return box_h


def _legend_mark(psp: Any, sym: str, cx: float, cy: float) -> None:
    layer = LAYER_SHEET
    if sym == "truck":
        body = [
            (cx - 5.5, cy - 1.6),
            (cx + 1.2, cy - 1.6),
            (cx + 1.2, cy + 1.6),
            (cx - 5.5, cy + 1.6),
        ]
        cab = [
            (cx + 1.2, cy - 2.0),
            (cx + 5.2, cy - 1.2),
            (cx + 5.8, cy),
            (cx + 5.2, cy + 1.2),
            (cx + 1.2, cy + 2.0),
        ]
        psp.add_lwpolyline(body, close=True, dxfattribs={"layer": layer})
        psp.add_lwpolyline(cab, close=True, dxfattribs={"layer": layer})
    elif sym == "parking":
        box = [
            (cx - 5.5, cy - 2.2),
            (cx + 5.5, cy - 2.2),
            (cx + 5.5, cy + 2.2),
            (cx - 5.5, cy + 2.2),
        ]
        psp.add_lwpolyline(box, close=True, dxfattribs={"layer": layer})
    elif sym in {"dc_320kw", "dc_160kw", "dc_120kw"}:
        sq = [
            (cx - 2.2, cy - 2.2),
            (cx + 2.2, cy - 2.2),
            (cx + 2.2, cy + 2.2),
            (cx - 2.2, cy + 2.2),
        ]
        psp.add_solid(sq, dxfattribs={"layer": layer})
    elif sym == "box_transformer":
        box = [
            (cx - 4.4, cy - 3.0),
            (cx + 4.4, cy - 3.0),
            (cx + 4.4, cy + 3.0),
            (cx - 4.4, cy + 3.0),
        ]
        psp.add_lwpolyline(box, close=True, dxfattribs={"layer": layer})
        psp.add_circle((cx, cy + 1.1), 1.2, dxfattribs={"layer": layer})
        psp.add_circle((cx, cy - 1.1), 1.2, dxfattribs={"layer": layer})
    elif sym == "fire_hydrant":
        psp.add_circle((cx, cy), 2.4, dxfattribs={"layer": layer})
        psp.add_line((cx - 2.4, cy), (cx + 2.4, cy), dxfattribs={"layer": layer})
        psp.add_line((cx, cy - 2.4), (cx, cy + 2.4), dxfattribs={"layer": layer})
    elif sym == "cable_well":
        box = [
            (cx - 2.6, cy - 2.6),
            (cx + 2.6, cy - 2.6),
            (cx + 2.6, cy + 2.6),
            (cx - 2.6, cy + 2.6),
        ]
        psp.add_lwpolyline(box, close=True, dxfattribs={"layer": layer})
        psp.add_circle((cx, cy), 1.4, dxfattribs={"layer": layer})
    elif sym == "vent_grille":
        box = [
            (cx - 3.6, cy - 2.0),
            (cx + 3.6, cy - 2.0),
            (cx + 3.6, cy + 2.0),
            (cx - 3.6, cy + 2.0),
        ]
        psp.add_lwpolyline(box, close=True, dxfattribs={"layer": layer})
        psp.add_line((cx - 3.6, cy - 2.0), (cx + 3.6, cy + 2.0), dxfattribs={"layer": layer})
        psp.add_line((cx - 3.6, cy + 2.0), (cx + 3.6, cy - 2.0), dxfattribs={"layer": layer})
    elif sym == "ring_cabinet":
        box = [
            (cx - 3.4, cy - 2.4),
            (cx + 3.4, cy - 2.4),
            (cx + 3.4, cy + 2.4),
            (cx - 3.4, cy + 2.4),
        ]
        psp.add_lwpolyline(box, close=True, dxfattribs={"layer": layer})
    else:
        psp.add_circle((cx, cy), 2.2, dxfattribs={"layer": layer})


def _north(psp: Any, pw: float, ph: float) -> None:
    insert = (pw - 22.0, ph - 28.0)
    psp.add_blockref(
        BLOCK_NORTH,
        insert=insert,
        dxfattribs={"layer": LAYER_SHEET, "xscale": 1.3, "yscale": 1.3},
    )
    _text(psp, "北", (insert[0], insert[1] + 16.0), 3.2, align="CENTER")


def _text(psp: Any, text: str, xy: tuple[float, float], height: float, *, align: str) -> None:
    if not text:
        return
    entity = psp.add_text(
        text,
        height=height,
        dxfattribs={"layer": LAYER_SHEET, "style": STYLE_CN},
    )
    mapping = {
        "LEFT": TextEntityAlignment.MIDDLE_LEFT,
        "CENTER": TextEntityAlignment.MIDDLE_CENTER,
        "RIGHT": TextEntityAlignment.MIDDLE_RIGHT,
    }
    entity.set_placement(xy, align=mapping.get(align, TextEntityAlignment.MIDDLE_LEFT))
