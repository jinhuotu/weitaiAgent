"""模型空间符号块。块内单位毫米，原点在几何中心（建筑块除外，原点在西南角）。"""

from __future__ import annotations

from typing import Any

from api.services.layouts.cad_export.layers import (
    BLOCK_AC_PILE,
    BLOCK_ARROW,
    BLOCK_BUILDING,
    BLOCK_CAR,
    BLOCK_DC_PILE,
    BLOCK_HYDRANT,
    BLOCK_LV,
    BLOCK_METER,
    BLOCK_NORTH,
    BLOCK_RING,
    BLOCK_TRANSFORMER,
    BLOCK_TREE,
    BLOCK_TRUCK,
    BLOCK_VENT,
    BLOCK_WELL,
)
from api.services.layouts.cad_export.units import MM_PER_M
from api.services.layouts.symbols import _TRUCK_LEN, _TRUCK_WID, _car_locals, _truck_locals


def setup_blocks(doc: Any) -> None:
    _vehicle_block(doc, BLOCK_TRUCK, _truck_locals(_TRUCK_LEN, _TRUCK_WID))
    _vehicle_block(doc, BLOCK_CAR, _car_locals(4.55, 1.78))
    _dc_pile_block(doc)
    _ac_pile_block(doc)
    _transformer_block(doc)
    _ring_block(doc)
    _building_block(doc)
    _arrow_block(doc)
    _north_block(doc)
    _tree_block(doc)
    _hydrant_block(doc)
    _well_block(doc)
    _vent_block(doc)
    _lv_block(doc)
    _meter_block(doc)


def _lw(blk: Any, pts: list[tuple[float, float]], *, close: bool = True) -> None:
    if len(pts) < 2:
        return
    blk.add_lwpolyline(pts, close=close, dxfattribs={"layer": "0", "color": 256})


def _vehicle_block(doc: Any, name: str, rings: list[list[tuple[float, float]]]) -> None:
    if name in doc.blocks:
        return
    blk = doc.blocks.new(name=name)
    for ring in rings:
        pts = [(a * MM_PER_M, c * MM_PER_M) for a, c in ring]
        _lw(blk, pts, close=True)


def _dc_pile_block(doc: Any) -> None:
    if BLOCK_DC_PILE in doc.blocks:
        return
    blk = doc.blocks.new(name=BLOCK_DC_PILE)
    s = 450.0
    pts = [(-s, -s), (s, -s), (s, s), (-s, s)]
    blk.add_solid(pts, dxfattribs={"layer": "0", "color": 256})
    _lw(blk, pts, close=True)


def _ac_pile_block(doc: Any) -> None:
    if BLOCK_AC_PILE in doc.blocks:
        return
    blk = doc.blocks.new(name=BLOCK_AC_PILE)
    s = 450.0
    _lw(blk, [(-s, -s), (s, -s), (s, s), (-s, s)], close=True)


def _transformer_block(doc: Any) -> None:
    """方框 + 两圈：施工图常见箱变符号。"""
    if BLOCK_TRANSFORMER in doc.blocks:
        return
    blk = doc.blocks.new(name=BLOCK_TRANSFORMER)
    w, h = 2200.0, 1540.0
    _lw(blk, [(-w, -h), (w, -h), (w, h), (-w, h)], close=True)
    r = 520.0
    blk.add_circle((0.0, 420.0), r, dxfattribs={"layer": "0", "color": 256})
    blk.add_circle((0.0, -420.0), r, dxfattribs={"layer": "0", "color": 256})


def _ring_block(doc: Any) -> None:
    if BLOCK_RING in doc.blocks:
        return
    blk = doc.blocks.new(name=BLOCK_RING)
    w, h = 1600.0, 1400.0
    _lw(blk, [(-w, -h), (w, -h), (w, h), (-w, h)], close=True)


def _building_block(doc: Any) -> None:
    if BLOCK_BUILDING in doc.blocks:
        return
    blk = doc.blocks.new(name=BLOCK_BUILDING)
    _lw(blk, [(0.0, 0.0), (1000.0, 0.0), (1000.0, 1000.0), (0.0, 1000.0)], close=True)


def _arrow_block(doc: Any) -> None:
    """箭头沿 +Y，长 1000mm，原点在箭尾。"""
    if BLOCK_ARROW in doc.blocks:
        return
    blk = doc.blocks.new(name=BLOCK_ARROW)
    blk.add_line((0.0, 0.0), (0.0, 720.0), dxfattribs={"layer": "0", "color": 256})
    _lw(blk, [(-180.0, 520.0), (0.0, 1000.0), (180.0, 520.0)], close=True)


def _north_block(doc: Any) -> None:
    """指北针，约 10mm 高，给图纸空间用。"""
    if BLOCK_NORTH in doc.blocks:
        return
    blk = doc.blocks.new(name=BLOCK_NORTH)
    _lw(blk, [(0.0, 10.0), (-3.2, -4.5), (0.0, -1.2), (3.2, -4.5)], close=True)
    blk.add_line((0.0, -4.5), (0.0, -6.5), dxfattribs={"layer": "0", "color": 256})


def _tree_block(doc: Any) -> None:
    if BLOCK_TREE in doc.blocks:
        return
    blk = doc.blocks.new(name=BLOCK_TREE)
    blk.add_circle((0.0, 0.0), 700.0, dxfattribs={"layer": "0", "color": 256})
    blk.add_circle((0.0, 0.0), 180.0, dxfattribs={"layer": "0", "color": 256})
    _lw(blk, [(-700.0, 0.0), (700.0, 0.0)], close=False)
    _lw(blk, [(0.0, -700.0), (0.0, 700.0)], close=False)


def _hydrant_block(doc: Any) -> None:
    """平面消火栓：圆 + 十字。"""
    if BLOCK_HYDRANT in doc.blocks:
        return
    blk = doc.blocks.new(name=BLOCK_HYDRANT)
    blk.add_circle((0.0, 0.0), 450.0, dxfattribs={"layer": "0", "color": 256})
    _lw(blk, [(-450.0, 0.0), (450.0, 0.0)], close=False)
    _lw(blk, [(0.0, -450.0), (0.0, 450.0)], close=False)


def _well_block(doc: Any) -> None:
    """电缆井 / 检查井。"""
    if BLOCK_WELL in doc.blocks:
        return
    blk = doc.blocks.new(name=BLOCK_WELL)
    s = 500.0
    _lw(blk, [(-s, -s), (s, -s), (s, s), (-s, s)], close=True)
    blk.add_circle((0.0, 0.0), 280.0, dxfattribs={"layer": "0", "color": 256})


def _vent_block(doc: Any) -> None:
    """通风格栅。"""
    if BLOCK_VENT in doc.blocks:
        return
    blk = doc.blocks.new(name=BLOCK_VENT)
    w, h = 900.0, 500.0
    _lw(blk, [(-w, -h), (w, -h), (w, h), (-w, h)], close=True)
    _lw(blk, [(-w, -h), (w, h)], close=False)
    _lw(blk, [(-w, h), (w, -h)], close=False)


def _lv_block(doc: Any) -> None:
    if BLOCK_LV in doc.blocks:
        return
    blk = doc.blocks.new(name=BLOCK_LV)
    w, h = 900.0, 700.0
    _lw(blk, [(-w, -h), (w, -h), (w, h), (-w, h)], close=True)
    _lw(blk, [(-w * 0.4, -h), (-w * 0.4, h)], close=False)


def _meter_block(doc: Any) -> None:
    if BLOCK_METER in doc.blocks:
        return
    blk = doc.blocks.new(name=BLOCK_METER)
    s = 500.0
    _lw(blk, [(-s, -s), (s, -s), (s, s), (-s, s)], close=True)
    blk.add_circle((0.0, 0.0), 220.0, dxfattribs={"layer": "0", "color": 256})
