"""DXF 图层：名字与黄金样本 cad.layers 对齐。"""

from __future__ import annotations

from typing import Any

LAYER_SITE = "WT-SITE"
LAYER_PARK = "WT-PARK"
LAYER_VEHICLE = "WT-VEHICLE"
LAYER_EQUIP = "WT-EQUIP"
LAYER_ROAD = "WT-ROAD"
LAYER_DIM = "WT-DIM"
LAYER_ANNO = "WT-ANNO"
LAYER_GREEN = "WT-GREEN"
LAYER_SHEET = "WT-SHEET"
LAYER_CABLE = "WT-CABLE"
LAYER_META = "WT-META"

# name, ACI color, lineweight (100ths of mm)
_LAYERS: tuple[tuple[str, int, int], ...] = (
    (LAYER_SITE, 7, 50),
    (LAYER_PARK, 4, 25),
    (LAYER_VEHICLE, 4, 18),
    (LAYER_EQUIP, 1, 25),
    (LAYER_ROAD, 7, 25),
    (LAYER_DIM, 3, 18),
    (LAYER_ANNO, 7, 18),
    (LAYER_GREEN, 3, 18),
    (LAYER_SHEET, 7, 35),
    (LAYER_CABLE, 5, 25),
    (LAYER_META, 8, 13),
)

STYLE_CN = "WT_CN"
DIMSTYLE = "WT_DIM"
LAYOUT_NAME = "充电站平面布置图"

BLOCK_TRUCK = "WT_TRUCK"
BLOCK_CAR = "WT_CAR"
BLOCK_DC_PILE = "WT_DC_PILE"
BLOCK_AC_PILE = "WT_AC_PILE"
BLOCK_TRANSFORMER = "WT_TRANSFORMER"
BLOCK_RING = "WT_RING"
BLOCK_BUILDING = "WT_BUILDING"
BLOCK_ARROW = "WT_ARROW"
BLOCK_NORTH = "WT_NORTH"
BLOCK_TREE = "WT_TREE"
BLOCK_HYDRANT = "WT_HYDRANT"
BLOCK_WELL = "WT_WELL"
BLOCK_VENT = "WT_VENT"
BLOCK_LV = "WT_LV"
BLOCK_METER = "WT_METER"


def setup_layers(doc: Any) -> None:
    for name, color, weight in _LAYERS:
        if name in doc.layers:
            layer = doc.layers.get(name)
            layer.dxf.color = color
            layer.dxf.lineweight = weight
        else:
            doc.layers.add(name, color=color, lineweight=weight)
    meta = doc.layers.get(LAYER_META)
    try:
        meta.off()
        meta.lock()
    except Exception:  # noqa: BLE001
        pass


def setup_text_style(doc: Any) -> None:
    if STYLE_CN in doc.styles:
        return
    doc.styles.add(STYLE_CN, font="simsun.ttf")


def setup_dimstyle(doc: Any, *, text_mm: float) -> None:
    """text_mm 是模型空间字高（毫米），按出图比例放大，使图纸上约 2.5mm。"""
    attribs = {
        "dimtxsty": STYLE_CN,
        "dimclrd": 3,
        "dimclre": 3,
        "dimclrt": 3,
        "dimtxt": float(text_mm),
        "dimexe": float(text_mm) * 0.5,
        "dimexo": float(text_mm) * 0.35,
        "dimasz": float(text_mm) * 0.7,
        "dimgap": float(text_mm) * 0.2,
        "dimdec": 2,
        "dimdsep": ord("."),
        "dimtad": 1,
        "dimtih": 0,
        "dimtoh": 0,
        "dimlwd": 18,
        "dimlwe": 18,
    }
    if DIMSTYLE in doc.dimstyles:
        style = doc.dimstyles.get(DIMSTYLE)
        for key, value in attribs.items():
            try:
                setattr(style.dxf, key, value)
            except Exception:  # noqa: BLE001
                continue
        return
    doc.dimstyles.new(DIMSTYLE, dxfattribs=attribs)
