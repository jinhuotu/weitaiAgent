"""布置图纸落盘文件名。"""

from __future__ import annotations

import re
from uuid import uuid4

from api.services.layouts.schema import EvChargingStationPlan

_BAD = re.compile(r"[^\w.\u4e00-\u9fff()\-]+", re.UNICODE)
_DASH = re.compile(r"-{2,}")


def layout_download_name(plan: EvChargingStationPlan, *, ext: str) -> str:
    """给浏览器「另存为」用的可读名，不含随机后缀。"""
    stem = _stem(plan) or "充电站平面布置图"
    suffix = ext.lstrip(".").lower() or "dxf"
    return f"{stem}.{suffix}"


def layout_stored_name(plan: EvChargingStationPlan, *, ext: str) -> str:
    """磁盘唯一名：可读 stem + 短哈希。"""
    stem = _stem(plan) or "layout"
    suffix = ext.lstrip(".").lower() or "dxf"
    return f"{stem}-{uuid4().hex[:8]}.{suffix}"


def _stem(plan: EvChargingStationPlan) -> str:
    tb = plan.titleBlock
    parts = [tb.project.strip(), tb.title.strip(), tb.sheetNo.strip()]
    merged = "-".join(p for p in parts if p)
    cleaned = _DASH.sub("-", _BAD.sub("-", merged)).strip("-._")
    return cleaned[:72]
