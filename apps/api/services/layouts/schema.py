"""充电站平面布置 JSON：工作流 LLM 产出、layout_out 消费。

坐标系：场地西南角为原点，X 向东（右），Y 向北（上），单位米。
车位用「整排参数」展开，不要逐个写坐标。
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = "1"
PLAN_KIND = "ev_charging_station_plan"

Side = Literal["north", "south", "east", "west"]
Along = Literal["x", "y"]
ChargerKind = Literal["dc_320kw", "dc_160kw", "dc_120kw", "ac_14kw", "none"]
ChargerSide = Literal["head", "tail", "left", "right"]
BuildingKind = Literal["building", "factory", "carport", "demolish"]
Voltage = Literal["10kv", "0.4kv"]
EquipmentKind = Literal[
    "ring_cabinet",
    "box_transformer",
    "ring_box_transformer",
    "lv_cabinet",
    "hv_meter",
    "group_host",
    "dc_320kw",
    "dc_160kw",
    "dc_120kw",
    "ac_14kw",
]
LegendSymbol = Literal[
    "ring_cabinet",
    "box_transformer",
    "group_host",
    "dc_320kw",
    "dc_160kw",
    "dc_120kw",
    "ac_14kw",
    "parking",
    "truck",
    "greenery",
    "tree",
]

_CHARGER_ALIASES = {
    "dc_320kw": "dc_320kw",
    "dc320kw": "dc_320kw",
    "320kw": "dc_320kw",
    "400kw": "dc_320kw",
    "dc_400kw": "dc_320kw",
    "dc400kw": "dc_320kw",
    "dc_160kw": "dc_160kw",
    "dc160kw": "dc_160kw",
    "160kw": "dc_160kw",
    "dc_120kw": "dc_120kw",
    "dc120kw": "dc_120kw",
    "120kw": "dc_120kw",
    "ac_14kw": "ac_14kw",
    "ac14kw": "ac_14kw",
    "14kw": "ac_14kw",
    "ac_7kw": "ac_14kw",
    "7kw": "ac_14kw",
    "none": "none",
}
_EQUIP_ALIASES = {
    **_CHARGER_ALIASES,
    "hw": "ring_cabinet",
    "rmu": "ring_cabinet",
    "ring_cabinet": "ring_cabinet",
    "ring_main_unit": "ring_cabinet",
    "box_transformer": "box_transformer",
    "box_type_substation": "box_transformer",
    "ring_box_transformer": "ring_box_transformer",
    "lv_cabinet": "lv_cabinet",
    "dp": "lv_cabinet",
    "hv_meter": "hv_meter",
    "jl": "hv_meter",
    "group_host": "group_host",
    "group_charger": "group_host",
    "host_cabinet": "group_host",
    "group_charging_host": "group_host",
}
_LEGEND_ALIASES = {
    "ring_cabinet": "ring_cabinet",
    "hw": "ring_cabinet",
    "rmu": "ring_cabinet",
    "box_transformer": "box_transformer",
    "ring_box_transformer": "box_transformer",
    "substation": "box_transformer",
    "group_host": "group_host",
    "group_charger": "group_host",
    "host_cabinet": "group_host",
    "dc_320kw": "dc_320kw",
    "dc_160kw": "dc_160kw",
    "dc_120kw": "dc_120kw",
    "ac_14kw": "ac_14kw",
    "parking": "parking",
    "parking_space": "parking",
    "stall": "parking",
    "car": "parking",
    "truck": "truck",
    "heavy_truck": "truck",
    "greenery": "greenery",
    "green": "greenery",
    "tree": "tree",
    "trees": "tree",
}
_LEGEND_DROP = {
    "cable_trench",
    "trench",
    "cable",
    "cables",
    "north",
    "north_arrow",
    "gate",
    "road",
    "internal_road",
}


def _norm_token(value: Any) -> str:
    raw = str(value or "").strip().lower()
    return re.sub(r"[^a-z0-9]+", "_", raw).strip("_")


def coerce_charger_type(value: Any) -> Any:
    key = _norm_token(value)
    if not key:
        return "none"
    if key in _CHARGER_ALIASES:
        return _CHARGER_ALIASES[key]
    if "160" in key and "ac" not in key:
        return "dc_160kw"
    if "120" in key and "ac" not in key:
        return "dc_120kw"
    if "400" in key or "320" in key:
        return "dc_320kw"
    if "14" in key:
        return "ac_14kw"
    if key in {"none", "null", "no"}:
        return "none"
    return value


def coerce_equipment_type(value: Any) -> Any:
    raw = str(value or "")
    if any(tok in raw for tok in ("群冲", "群充", "主机柜")):
        return "group_host"
    key = _norm_token(value)
    if key in _EQUIP_ALIASES:
        return _EQUIP_ALIASES[key]
    return coerce_charger_type(value)


def coerce_optional_pitch_m(value: Any) -> Any:
    """LLM 常把未填间距写成 0；0/空值交给程序按车宽和斜角计算。"""
    if value is None:
        return None
    if isinstance(value, str):
        raw = value.strip().lower()
        if raw in {"", "null", "none", "undefined", "-"}:
            return None
        try:
            value = float(raw)
        except ValueError:
            return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if num < 1.6 or num > 40:
        return None
    return num


def _as_xy(value: Any) -> tuple[float, float] | None:
    if isinstance(value, dict) and "x" in value and "y" in value:
        try:
            return float(value["x"]), float(value["y"])
        except (TypeError, ValueError):
            return None
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        try:
            return float(value[0]), float(value[1])
        except (TypeError, ValueError):
            return None
    return None


def coerce_polyline_points(value: Any) -> list[dict[str, float]] | None:
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None
    pts: list[dict[str, float]] = []
    for item in value:
        xy = _as_xy(item)
        if xy is None:
            return None
        pts.append({"x": xy[0], "y": xy[1]})
    return pts if len(pts) >= 2 else None


def polyline_from_mapping(item: dict[str, Any]) -> list[dict[str, float]] | None:
    for key in ("polyline", "points", "path", "coords", "vertices", "line"):
        pts = coerce_polyline_points(item.get(key))
        if pts:
            return pts
    start = _as_xy(item.get("start") or item.get("from"))
    end = _as_xy(item.get("end") or item.get("to"))
    if start and end:
        return [{"x": start[0], "y": start[1]}, {"x": end[0], "y": end[1]}]
    return None


def drop_items_missing_polyline(value: Any) -> Any:
    """LLM 常只写沟截面/长度、不写折线；几何由 packer 按桩头重布。"""
    if value in (None, "", False):
        return []
    if not isinstance(value, list):
        return value
    out: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        pts = polyline_from_mapping(item)
        if not pts:
            continue
        out.append({**item, "polyline": pts})
    return out


def drop_items_missing_polygon(value: Any) -> Any:
    if value in (None, "", False):
        return []
    if not isinstance(value, list):
        return value
    out: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        pts = coerce_polyline_points(
            item.get("polygon") or item.get("points") or item.get("path")
        )
        if not pts or len(pts) < 3:
            continue
        out.append({**item, "polygon": pts})
    return out


def coerce_charger_start_no(value: Any) -> Any:
    """桩号从 1 起编。LLM 常把未确定的 startNo 写成 0。"""
    if value is None:
        return 1
    if isinstance(value, str):
        raw = value.strip().lower()
        if raw in {"", "null", "none", "undefined", "-"}:
            return 1
        try:
            value = float(raw)
        except ValueError:
            return 1
    try:
        num = int(float(value))
    except (TypeError, ValueError):
        return 1
    if num < 1:
        return 1
    if num > 999:
        return 999
    return num


def coerce_charger_side(value: Any) -> Any:
    """head/tail=车头车尾；left/right=车位宽向两侧（东西墙贴墙用）。"""
    if value is None or value == "":
        return "head"
    if isinstance(value, dict):
        for key in ("side", "name", "dir", "facing"):
            if key in value:
                return coerce_charger_side(value[key])
    s = str(value).strip().lower()
    mapping = {
        "head": "head",
        "tail": "tail",
        "车头": "head",
        "车尾": "tail",
        "front": "head",
        "back": "tail",
        "left": "left",
        "right": "right",
        "左": "left",
        "右": "right",
    }
    if s in mapping:
        return mapping[s]
    if "tail" in s or "尾" in s:
        return "tail"
    if "left" in s or "左" in s:
        return "left"
    if "right" in s or "右" in s:
        return "right"
    return "head"


def coerce_legend_list(value: Any) -> Any:
    if not isinstance(value, list):
        return value
    out: list[str] = []
    seen: set[str] = set()
    for item in value:
        key = _norm_token(item)
        if key in _LEGEND_DROP:
            continue
        mapped = _LEGEND_ALIASES.get(key)
        if mapped is None:
            mapped = coerce_charger_type(item)
            if mapped not in {
                "ring_cabinet",
                "box_transformer",
                "group_host",
                "dc_320kw",
                "dc_160kw",
                "dc_120kw",
                "ac_14kw",
                "parking",
                "truck",
                "greenery",
                "tree",
            }:
                continue
        if mapped in seen:
            continue
        seen.add(mapped)
        out.append(mapped)
    return out


class PlanModel(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class PointM(PlanModel):
    x: float
    y: float


class RectM(PlanModel):
    """矩形：原点为旋转前的西南角；angleDeg 绕该点逆时针。"""

    x: float
    y: float
    w: float = Field(gt=0, le=500)
    h: float = Field(gt=0, le=500)
    angleDeg: float = 0


def coerce_rect_m(value: Any) -> Any:
    """LLM 常把建筑写成 x/y/w/h 平铺，或 bbox/box/polygon，而不是 rect。"""
    if value is None or value == "":
        return value
    if isinstance(value, (list, tuple)) and len(value) >= 4:
        try:
            return {
                "x": float(value[0]),
                "y": float(value[1]),
                "w": float(value[2]),
                "h": float(value[3]),
                "angleDeg": float(value[4]) if len(value) > 4 else 0,
            }
        except (TypeError, ValueError):
            return value
    if not isinstance(value, dict):
        return value
    if all(k in value for k in ("x", "y", "w", "h")):
        return value
    for key in ("rect", "bbox", "box", "bounds", "area"):
        nested = value.get(key)
        if nested is not None and nested is not value:
            got = coerce_rect_m(nested)
            if isinstance(got, dict) and all(k in got for k in ("x", "y", "w", "h")):
                return got
    # 平铺字段：width/height 或 wM/hM
    try:
        x = value.get("x", value.get("left", value.get("x0")))
        y = value.get("y", value.get("bottom", value.get("y0")))
        w = value.get("w", value.get("width", value.get("widthM", value.get("wM"))))
        h = value.get("h", value.get("height", value.get("heightM", value.get("hM"))))
        if None not in (x, y, w, h):
            return {
                "x": float(x),
                "y": float(y),
                "w": float(w),
                "h": float(h),
                "angleDeg": float(value.get("angleDeg") or value.get("angle") or 0),
            }
        # 对角点 x0,y0,x1,y1
        x0 = value.get("x0", value.get("minX"))
        y0 = value.get("y0", value.get("minY"))
        x1 = value.get("x1", value.get("maxX"))
        y1 = value.get("y1", value.get("maxY"))
        if None not in (x0, y0, x1, y1):
            fx0, fy0, fx1, fy1 = float(x0), float(y0), float(x1), float(y1)
            return {
                "x": min(fx0, fx1),
                "y": min(fy0, fy1),
                "w": abs(fx1 - fx0) or 1.0,
                "h": abs(fy1 - fy0) or 1.0,
                "angleDeg": 0,
            }
        pts = coerce_polyline_points(
            value.get("polygon") or value.get("points") or value.get("path")
        )
        if pts and len(pts) >= 3:
            xs = [p["x"] for p in pts]
            ys = [p["y"] for p in pts]
            return {
                "x": min(xs),
                "y": min(ys),
                "w": max(1.0, max(xs) - min(xs)),
                "h": max(1.0, max(ys) - min(ys)),
                "angleDeg": 0,
            }
    except (TypeError, ValueError):
        return value
    return value


def coerce_building_item(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    out = dict(value)
    rect = coerce_rect_m(out.get("rect") if "rect" in out else out)
    if isinstance(rect, dict) and all(k in rect for k in ("x", "y", "w", "h")):
        out["rect"] = rect
        return out
    return out


def drop_buildings_missing_rect(value: Any) -> Any:
    """缺几何的建筑丢掉，避免整张布置因一条坏 building 校验失败。"""
    if value in (None, "", False):
        return []
    if not isinstance(value, list):
        return value
    out: list[dict[str, Any]] = []
    for i, item in enumerate(value):
        if not isinstance(item, dict):
            continue
        fixed = coerce_building_item(item)
        rect = fixed.get("rect") if isinstance(fixed, dict) else None
        if not isinstance(rect, dict) or not all(k in rect for k in ("x", "y", "w", "h")):
            continue
        if not fixed.get("id"):
            fixed = {**fixed, "id": f"b{i + 1}"}
        out.append(fixed)
    return out


def clamp_gate_to_span(offset: float, width: float, span: float) -> tuple[float, float]:
    """把出入口收进场地边长。LLM 常照抄示例 offsetM=38 套到更小的场地。"""
    edge = max(float(span), 0.5)
    opening = min(max(float(width), 0.5), min(80.0, edge))
    start = max(0.0, min(float(offset), edge - opening))
    if start + opening > edge + 1e-9:
        opening = max(0.5, edge - start)
    return start, opening


def coerce_gate_side(value: Any) -> Any:
    """LLM 常把 side 写成 南侧 / southeast / {name: east}。"""
    if value is None or value == "":
        return "south"
    if isinstance(value, dict):
        for key in ("side", "name", "dir", "direction", "facing"):
            if value.get(key) is not None:
                return coerce_gate_side(value[key])
        return "south"
    raw = str(value).strip().lower()
    if raw in {"null", "none", "undefined", "-"}:
        return "south"
    if any(tok in raw for tok in ("southeast", "south-east", "south_east", "东南", "右下")):
        return "south"
    if any(tok in raw for tok in ("southwest", "south-west", "south_west", "西南", "左下")):
        return "south"
    if any(tok in raw for tok in ("northeast", "north-east", "north_east", "东北", "右上")):
        return "east"
    if any(tok in raw for tok in ("northwest", "north-west", "north_west", "西北", "左上")):
        return "west"
    if any(tok in raw for tok in ("south", "南")):
        return "south"
    if any(tok in raw for tok in ("north", "北")):
        return "north"
    if any(tok in raw for tok in ("east", "东")):
        return "east"
    if any(tok in raw for tok in ("west", "西")):
        return "west"
    return "south"


def coerce_gate(value: Any) -> Any:
    """LLM 常把 site.gate 写成 '南侧' / 'southeast' / ['east', 10, 8]，校验需要对象。"""
    if value is None or value is False:
        return None
    if isinstance(value, GateSpec):
        return value
    if isinstance(value, str):
        raw = value.strip()
        if raw.lower() in {"", "null", "none", "undefined", "-"}:
            return None
        low = raw.lower()
        # 东南/右下：用很大的 offset，clamp 后落到该边东端，而不是 0（西端）。
        if any(tok in raw or tok in low for tok in ("东南", "右下", "southeast", "south-east")):
            offset = 10_000.0
        elif any(tok in raw or tok in low for tok in ("西南", "左下", "southwest", "south-west")):
            offset = 0.0
        else:
            offset = 0.0
        return {"side": coerce_gate_side(raw), "offsetM": offset, "widthM": 8, "label": "出入口"}
    if isinstance(value, (int, float, bool)):
        return None
    if isinstance(value, (list, tuple)):
        if not value:
            return None
        first = value[0]
        if isinstance(first, dict):
            return coerce_gate(first)
        out: dict[str, Any] = {
            "side": coerce_gate_side(first),
            "offsetM": 0,
            "widthM": 8,
            "label": "出入口",
        }
        if len(value) >= 2:
            try:
                out["offsetM"] = float(value[1])
            except (TypeError, ValueError):
                pass
        if len(value) >= 3:
            try:
                out["widthM"] = float(value[2])
            except (TypeError, ValueError):
                pass
        return out
    if isinstance(value, dict):
        out = dict(value)
        if "offsetM" not in out:
            for key in ("offset", "offset_m", "x"):
                if key in out:
                    out["offsetM"] = out[key]
                    break
        if "widthM" not in out:
            for key in ("width", "width_m", "w"):
                if key in out:
                    out["widthM"] = out[key]
                    break
        if "side" in out:
            out["side"] = coerce_gate_side(out["side"])
        elif "direction" in out:
            out["side"] = coerce_gate_side(out["direction"])
        return out
    return None


class GateSpec(PlanModel):
    side: Side = "south"
    offsetM: float = Field(default=0, ge=0)
    widthM: float = Field(default=8, gt=0, le=80)
    label: str = "出入口"
    roadLabel: str = ""

    @field_validator("side", mode="before")
    @classmethod
    def _coerce_side(cls, v: Any) -> Any:
        return coerce_gate_side(v)


class SiteSpec(PlanModel):
    widthM: float = Field(gt=0, le=500)
    heightM: float = Field(gt=0, le=500)
    northDeg: float = 0
    gate: GateSpec | None = None
    gates: list[GateSpec] = Field(default_factory=list, max_length=8)
    polygon: list[PointM] = Field(default_factory=list, max_length=40)

    @field_validator("polygon", mode="before")
    @classmethod
    def _coerce_polygon(cls, v: Any) -> Any:
        if v in (None, "", False):
            return []
        pts = coerce_polyline_points(v)
        if not pts or len(pts) < 3:
            return []
        return pts

    @model_validator(mode="before")
    @classmethod
    def _split_gate_array(cls, data: Any) -> Any:
        """site.gate 写成对象数组时拆成多门；['east', 10, 8] 仍是单门。"""
        if not isinstance(data, dict):
            return data
        raw = data.get("gate")
        extra = data.get("gates")
        items: list[Any] = []
        if isinstance(raw, (list, tuple)) and raw and isinstance(raw[0], dict):
            items.extend(raw)
        elif raw not in (None, "", False):
            items.append(raw)
        if isinstance(extra, (list, tuple)):
            items.extend(extra)
        elif extra not in (None, "", False):
            items.append(extra)
        if isinstance(raw, (list, tuple)) and raw and isinstance(raw[0], dict):
            return {**data, "gate": items[0], "gates": items}
        if extra not in (None, "", False) and "gates" not in data:
            return {**data, "gates": items}
        poly = data.get("polygon")
        if poly in (None, "", False, []) :
            for key in ("boundary", "outline", "contour"):
                alt = data.get(key)
                if alt not in (None, "", False, []):
                    data = {**data, "polygon": alt}
                    break
        return data

    @field_validator("gate", mode="before")
    @classmethod
    def _coerce_gate(cls, v: Any) -> Any:
        return coerce_gate(v)

    @field_validator("gates", mode="before")
    @classmethod
    def _coerce_gates(cls, v: Any) -> Any:
        if v in (None, "", False):
            return []
        if isinstance(v, (list, tuple)):
            out: list[Any] = []
            for item in v:
                one = coerce_gate(item)
                if one not in (None, "", False):
                    out.append(one)
            return out
        one = coerce_gate(v)
        return [one] if one not in (None, "", False) else []


def collect_site_gates(site: SiteSpec) -> list[GateSpec]:
    items: list[GateSpec] = []
    seen: set[tuple[str, float, float]] = set()
    for g in [site.gate, *list(site.gates or [])]:
        if g is None:
            continue
        key = (g.side, round(float(g.offsetM), 1), round(float(g.widthM), 1))
        if key in seen:
            continue
        seen.add(key)
        items.append(g)
    return items


def site_boundary_m(site: SiteSpec) -> list[tuple[float, float]]:
    """不规则用地用 polygon；否则退回矩形外框。"""
    pts = [(p.x, p.y) for p in (site.polygon or [])]
    if len(pts) >= 3:
        if pts[0] != pts[-1]:
            pts = pts + [pts[0]]
        return pts
    w, h = float(site.widthM), float(site.heightM)
    return [(0.0, 0.0), (w, 0.0), (w, h), (0.0, h), (0.0, 0.0)]


def site_is_irregular(site: SiteSpec) -> bool:
    return len(site.polygon or []) >= 3


def set_site_gates(site: SiteSpec, gates: list[GateSpec]) -> None:
    uniq: list[GateSpec] = []
    seen: set[tuple[str, float, float]] = set()
    for g in gates:
        key = (g.side, round(float(g.offsetM), 1), round(float(g.widthM), 1))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(g)
    site.gate = uniq[0] if uniq else None
    site.gates = uniq[1:]


class TitleBlock(PlanModel):
    title: str = "充电站平面布置图"
    sheetNo: str = "001"
    project: str = ""


class BuildingSpec(PlanModel):
    id: str = Field(min_length=1, max_length=64)
    label: str = "建筑"
    kind: BuildingKind = "building"
    rect: RectM

    @field_validator("rect", mode="before")
    @classmethod
    def _coerce_rect(cls, v: Any) -> Any:
        return coerce_rect_m(v)

    @model_validator(mode="before")
    @classmethod
    def _lift_flat_rect(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        return coerce_building_item(data)


class ChargerOnRow(PlanModel):
    type: ChargerKind = "dc_320kw"
    startNo: int = Field(default=1, ge=1, le=999)
    side: ChargerSide = "head"

    @field_validator("type", mode="before")
    @classmethod
    def _coerce_type(cls, v: Any) -> Any:
        return coerce_charger_type(v)

    @field_validator("startNo", mode="before")
    @classmethod
    def _coerce_start_no(cls, v: Any) -> Any:
        return coerce_charger_start_no(v)

    @field_validator("side", mode="before")
    @classmethod
    def _coerce_side(cls, v: Any) -> Any:
        return coerce_charger_side(v)


class AisleSpec(PlanModel):
    """车道中心线 + 宽度；优先由 pack 按规则生成，LLM 可不填。"""

    id: str = Field(min_length=1, max_length=64)
    centerline: list[PointM] = Field(min_length=2, max_length=40)
    widthM: float = Field(gt=0, le=40)
    kind: Literal["drive", "fire", "service"] = "drive"
    label: str = ""


class SheetStyle(PlanModel):
    """图纸样式：只影响渲染，不改变工程几何。"""

    scale: str = "1:200"
    paper: Literal["A3", "A2", "A1"] = "A3"
    showGrid: bool = False
    showDimensions: bool = True
    showNorthArrow: bool = True
    showTitleBlock: bool = True
    showLegend: bool = True


class ParkingRowSpec(PlanModel):
    """一排斜列/平行车位。origin 为第一台车位西南角。

    along=x|y 表示整排沿场地轴平移（平行道路），angleDeg 只旋转单台车位。
    stallWidthM = 车位短边；stallLengthM = 停车深度。
    不要把 CAD 图面上「4m×17m」（常含通道）整段写进 stallLengthM。
    """

    id: str = Field(min_length=1, max_length=64)
    stalls: int = Field(ge=1, le=80)
    stallWidthM: float = Field(default=2.5, gt=0.5, le=20)
    stallLengthM: float = Field(default=5.5, gt=1, le=40)
    angleDeg: float = 0.0
    origin: PointM
    along: Along = "x"
    pitchM: float | None = Field(default=None, gt=0, le=40)
    charger: ChargerOnRow | None = None
    labelPrefix: str = "直流充电桩"

    @model_validator(mode="before")
    @classmethod
    def _lift_charger_fields(cls, data: Any) -> Any:
        """兼容模型把功率写在 chargerType / 字符串 charger 上，避免缺字段回落到默认 320kW。"""
        if not isinstance(data, dict):
            return data
        ch = data.get("charger")
        if isinstance(ch, str):
            ch = {"type": ch}
        elif not isinstance(ch, dict):
            ch = {}
        else:
            ch = dict(ch)
        if not ch.get("type"):
            for key in ("chargerType", "dcType", "type"):
                if key == "type":
                    continue
                if data.get(key):
                    ch["type"] = data[key]
                    break
            if not ch.get("type") and data.get("type") and not data.get("stalls"):
                ch["type"] = data["type"]
        if ch.get("startNo") in (None, "", 0) and data.get("startNo"):
            ch["startNo"] = data["startNo"]
        if ch:
            data = {**data, "charger": ch}
        return data

    @field_validator("pitchM", mode="before")
    @classmethod
    def _coerce_pitch(cls, v: Any) -> Any:
        return coerce_optional_pitch_m(v)


class EquipmentSpec(PlanModel):
    id: str = Field(min_length=1, max_length=64)
    type: EquipmentKind
    x: float
    y: float
    label: str = ""
    capacityKva: float | None = Field(default=None, ge=0, le=20000)

    @field_validator("type", mode="before")
    @classmethod
    def _coerce_type(cls, v: Any) -> Any:
        return coerce_equipment_type(v)


class PolylineSpec(PlanModel):
    id: str = Field(min_length=1, max_length=64)
    polyline: list[PointM] = Field(default_factory=list, max_length=80)
    label: str = ""


class TrenchSpec(PolylineSpec):
    widthMm: float = Field(default=1200, gt=0, le=5000)
    heightMm: float = Field(default=1100, gt=0, le=5000)
    lengthM: float | None = Field(default=None, ge=0, le=2000)


class CableSpec(PolylineSpec):
    voltage: Voltage = "0.4kv"


class TreeSpec(PlanModel):
    x: float
    y: float


class PolygonLabelSpec(PlanModel):
    polygon: list[PointM] = Field(min_length=3, max_length=40)
    label: str = ""


class EvChargingStationPlan(PlanModel):
    schemaVersion: Literal["1"] = SCHEMA_VERSION
    kind: Literal["ev_charging_station_plan"] = PLAN_KIND
    titleBlock: TitleBlock = Field(default_factory=TitleBlock)
    site: SiteSpec
    buildings: list[BuildingSpec] = Field(default_factory=list, max_length=40)
    parkingRows: list[ParkingRowSpec] = Field(default_factory=list, max_length=20)
    aisles: list[AisleSpec] = Field(default_factory=list, max_length=40)
    equipment: list[EquipmentSpec] = Field(default_factory=list, max_length=80)
    trenches: list[TrenchSpec] = Field(default_factory=list, max_length=20)
    cables: list[CableSpec] = Field(default_factory=list, max_length=40)
    trees: list[TreeSpec] = Field(default_factory=list, max_length=80)
    greenery: list[PolygonLabelSpec] = Field(default_factory=list, max_length=20)
    roads: list[PolygonLabelSpec] = Field(default_factory=list, max_length=10)
    legend: list[LegendSymbol] = Field(
        default_factory=lambda: [
            "ring_cabinet",
            "box_transformer",
            "dc_320kw",
            "parking",
            "greenery",
            "tree",
        ]
    )
    notes: list[str] = Field(default_factory=list, max_length=20)
    sheetStyle: SheetStyle = Field(default_factory=SheetStyle)

    @model_validator(mode="before")
    @classmethod
    def _lift_gate(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        site = data.get("site")
        if not isinstance(site, dict):
            return data
        if site.get("gates") in (None, "", False, []):
            raw_gates = data.get("gates")
            if isinstance(raw_gates, (list, tuple)) and raw_gates:
                site = {**site, "gates": list(raw_gates)}
                data = {**data, "site": site}
        if site.get("gate") not in (None, "", False):
            return data
        for key in ("gate", "entrance", "exit", "entranceGate"):
            raw = data.get(key)
            if raw in (None, "", False):
                continue
            if isinstance(raw, (dict, str, list, tuple)):
                merged = {**site, "gate": raw}
                return {**data, "site": merged}
        return data

    @field_validator("buildings", mode="before")
    @classmethod
    def _drop_broken_buildings(cls, v: Any) -> Any:
        return drop_buildings_missing_rect(v)

    @field_validator("trenches", "cables", mode="before")
    @classmethod
    def _drop_broken_polylines(cls, v: Any) -> Any:
        return drop_items_missing_polyline(v)

    @field_validator("aisles", mode="before")
    @classmethod
    def _drop_broken_aisles(cls, v: Any) -> Any:
        if v in (None, "", False):
            return []
        if not isinstance(v, list):
            return []
        kept: list[Any] = []
        for item in v:
            if not isinstance(item, dict):
                continue
            line = item.get("centerline")
            pts = coerce_polyline_points(line) if line is not None else None
            if not pts or len(pts) < 2:
                continue
            row = dict(item)
            row["centerline"] = pts
            kept.append(row)
        return kept

    @field_validator("greenery", "roads", mode="before")
    @classmethod
    def _drop_broken_polygons(cls, v: Any) -> Any:
        return drop_items_missing_polygon(v)

    @field_validator("legend", mode="before")
    @classmethod
    def _coerce_legend(cls, v: Any) -> Any:
        return coerce_legend_list(v)

    @field_validator("schemaVersion", mode="before")
    @classmethod
    def _coerce_version(cls, v: Any) -> Any:
        if v in {1, 1.0, "1.0"}:
            return "1"
        if isinstance(v, str) and v.strip() in {"1", "1.0"}:
            return "1"
        return v

    @model_validator(mode="after")
    def _site_fits_gate(self) -> EvChargingStationPlan:
        gates = collect_site_gates(self.site)
        if not gates:
            return self
        fitted: list[GateSpec] = []
        for gate in gates:
            span = self.site.widthM if gate.side in {"north", "south"} else self.site.heightM
            offset, width = clamp_gate_to_span(gate.offsetM, gate.widthM, span)
            if offset != gate.offsetM or width != gate.widthM:
                fitted.append(gate.model_copy(update={"offsetM": offset, "widthM": width}))
            else:
                fitted.append(gate)
        set_site_gates(self.site, fitted)
        return self


EXAMPLE_PLAN: dict[str, Any] = {
    "schemaVersion": "1",
    "kind": "ev_charging_station_plan",
    "titleBlock": {
        "title": "充电站平面布置图",
        "sheetNo": "002",
        "project": "示范站",
    },
    "site": {
        "widthM": 92,
        "heightM": 52,
        "northDeg": 0,
        "gate": {
            "side": "south",
            "offsetM": 38,
            "widthM": 10,
            "label": "出入口",
            "roadLabel": "S304省道",
        },
    },
    "buildings": [
        {
            "id": "factory",
            "label": "厂房",
            "kind": "factory",
            "rect": {"x": 62, "y": 38, "w": 28, "h": 12},
        },
        {
            "id": "carport",
            "label": "车棚(拆除)",
            "kind": "demolish",
            "rect": {"x": 2, "y": 2, "w": 10, "h": 6},
        },
    ],
    "parkingRows": [
        {
            "id": "trucks",
            "stalls": 4,
            "stallWidthM": 5.0,
            "stallLengthM": 17.0,
            "angleDeg": 0,
            "origin": {"x": 20, "y": 4},
            "along": "x",
            "charger": {"type": "dc_320kw", "startNo": 1, "side": "head"},
            "labelPrefix": "重卡直流桩",
        },
        {
            "id": "cars",
            "stalls": 8,
            "stallWidthM": 3.0,
            "stallLengthM": 6.0,
            "angleDeg": 0,
            "origin": {"x": 20, "y": 28},
            "along": "x",
            "charger": {"type": "dc_160kw", "startNo": 5, "side": "head"},
            "labelPrefix": "直流充电桩",
        },
    ],
    "equipment": [
        {
            "id": "tx1",
            "type": "ring_box_transformer",
            "x": 72,
            "y": 34,
            "label": "新建1#环网型箱变",
            "capacityKva": 1250,
        }
    ],
    "trenches": [
        {
            "id": "tr1",
            "widthMm": 1200,
            "heightMm": 1100,
            "lengthM": 70,
            "polyline": [{"x": 18, "y": 22}, {"x": 78, "y": 22}],
            "label": "电缆沟 1200(W)x1100(H)",
        }
    ],
    "cables": [],
    "trees": [{"x": 6, "y": 26}, {"x": 10, "y": 40}],
    "greenery": [
        {
            "polygon": [
                {"x": 2, "y": 44},
                {"x": 18, "y": 44},
                {"x": 18, "y": 50},
                {"x": 2, "y": 50},
            ],
            "label": "绿化",
        }
    ],
    "roads": [
        {
            "polygon": [
                {"x": 18, "y": 18},
                {"x": 60, "y": 18},
                {"x": 60, "y": 28},
                {"x": 18, "y": 28},
            ],
            "label": "内部道路",
        }
    ],
    "legend": [
        "ring_cabinet",
        "box_transformer",
        "dc_320kw",
        "dc_160kw",
        "parking",
        "greenery",
        "tree",
    ],
    "notes": [],
}
