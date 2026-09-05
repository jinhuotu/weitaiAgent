"""从 LLM / 工作流 output 抽出布置 JSON 并校验。"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import ValidationError

from api.services.layouts.schema import EvChargingStationPlan, drop_items_missing_polygon
from common.errors import AppError, ErrorCode

_OPTIONAL_LISTS = ("trenches", "cables", "greenery", "roads", "trees", "buildings")

_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)
_THINK_RE = re.compile(
    r"<(?:think|thinking|reason|reasoning)>[\s\S]*?</(?:think|thinking|reason|reasoning)>",
    re.IGNORECASE,
)
_THINK_OPEN_RE = re.compile(
    r"<(?:think|thinking|reason|reasoning)>[\s\S]*$",
    re.IGNORECASE,
)


def _strip_model_wrappers(text: str) -> str:
    raw = (text or "").replace("\ufeff", "")
    raw = _THINK_RE.sub("", raw)
    raw = _THINK_OPEN_RE.sub("", raw)
    raw = (
        raw.replace("“", '"')
        .replace("”", '"')
        .replace("‘", "'")
        .replace("’", "'")
        .replace("＇", "'")
        .replace("＂", '"')
    )
    return raw.strip()


def _sanitize_json_text(snippet: str) -> str:
    s = re.sub(r"/\*[\s\S]*?\*/", "", snippet)
    s = re.sub(r"(^|[^:])//[^\n]*", r"\1", s)
    s = re.sub(r",\s*([}\]])", r"\1", s)
    s = re.sub(r"\bNone\b", "null", s)
    s = re.sub(r"\bTrue\b", "true", s)
    s = re.sub(r"\bFalse\b", "false", s)
    return s


def _slice_balanced(snippet: str) -> str:
    depth = 0
    in_str = False
    escape = False
    end = -1
    for i, ch in enumerate(snippet):
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    if end < 0:
        raise json.JSONDecodeError("unbalanced", snippet, 0)
    return snippet[: end + 1]


def _loads_object(snippet: str) -> Any:
    last_err: Exception | None = None
    for blob in (snippet, _sanitize_json_text(snippet)):
        try:
            return json.loads(blob)
        except json.JSONDecodeError as exc:
            last_err = exc
        try:
            obj, _end = json.JSONDecoder().raw_decode(blob.lstrip())
            return obj
        except json.JSONDecodeError as exc:
            last_err = exc
        try:
            return json.loads(_slice_balanced(blob))
        except json.JSONDecodeError as exc:
            last_err = exc
    raise last_err or json.JSONDecodeError("invalid", snippet, 0)


def _iter_json_objects(text: str) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    i = 0
    while i < len(text):
        start = text.find("{", i)
        if start < 0:
            break
        try:
            obj = _loads_object(text[start:])
        except json.JSONDecodeError:
            i = start + 1
            continue
        if isinstance(obj, dict):
            found.append(obj)
            try:
                i = start + len(_slice_balanced(text[start:]))
            except json.JSONDecodeError:
                i = start + 1
        else:
            i = start + 1
    return found


def _prefer_layout_object(objs: list[dict[str, Any]]) -> dict[str, Any]:
    def score(item: dict[str, Any]) -> tuple[int, int]:
        kind = str(item.get("kind") or "")
        if kind == "ev_charging_station_plan" or "parkingRows" in item:
            return (3, len(item))
        if kind == "ev_charging_station_constraints":
            return (2, len(item))
        if "site" in item or "fleet" in item:
            return (1, len(item))
        return (0, len(item))

    return max(objs, key=score)


def _prefer_keyed_object(
    objs: list[dict[str, Any]], keys: tuple[str, ...]
) -> dict[str, Any]:
    def score(item: dict[str, Any]) -> tuple[int, int]:
        hits = sum(1 for k in keys if k in item)
        return (hits, len(item))

    return max(objs, key=score)


def extract_json_object(
    text: str,
    *,
    prefer_keys: tuple[str, ...] = (),
) -> dict[str, Any]:
    raw = _strip_model_wrappers(text)
    if not raw:
        raise AppError(ErrorCode.VALIDATION, "布置 JSON 为空", status_code=422)
    fenced = _FENCE_RE.findall(raw)
    blobs = [s.strip() for s in fenced if s.strip()]
    blobs.append(raw)
    found: list[dict[str, Any]] = []
    last_err = "未找到 JSON 对象"
    for blob in blobs:
        if "{" not in blob:
            continue
        objs = _iter_json_objects(blob)
        if objs:
            found.extend(objs)
        else:
            last_err = "JSON 无法解析"
    if found:
        if prefer_keys:
            return _prefer_keyed_object(found, prefer_keys)
        return _prefer_layout_object(found)
    raise AppError(ErrorCode.VALIDATION, last_err, status_code=422)


def _sanitize_layout_payload(payload: dict[str, Any]) -> dict[str, Any]:
    data = dict(payload)
    # 沟缆折线由程序按桩头重布；模型常只写截面，这里一律丢掉以免卡死出图。
    data["trenches"] = []
    data["cables"] = []
    from api.services.layouts.schema import drop_buildings_missing_rect

    data["greenery"] = drop_items_missing_polygon(data.get("greenery"))
    data["roads"] = drop_items_missing_polygon(data.get("roads"))
    data["buildings"] = drop_buildings_missing_rect(data.get("buildings"))
    return data


def parse_plan(source: Any) -> EvChargingStationPlan:
    if isinstance(source, EvChargingStationPlan):
        return source
    if isinstance(source, dict):
        payload = dict(source)
    else:
        payload = extract_json_object(str(source or ""))
    payload = _sanitize_layout_payload(payload)
    try:
        return EvChargingStationPlan.model_validate(payload)
    except ValidationError as exc:
        retry = dict(payload)
        stripped = False
        for err in exc.errors():
            loc = err.get("loc") or ()
            if loc and str(loc[0]) in _OPTIONAL_LISTS:
                retry[str(loc[0])] = []
                stripped = True
        if stripped:
            try:
                return EvChargingStationPlan.model_validate(retry)
            except ValidationError:
                pass
        raise AppError(
            ErrorCode.VALIDATION,
            f"布置 JSON 校验失败：{_format_pydantic(exc)}",
            status_code=422,
        ) from exc


def _format_pydantic(exc: ValidationError) -> str:
    parts: list[str] = []
    for err in exc.errors()[:6]:
        loc = ".".join(str(x) for x in err.get("loc") or [])
        msg = str(err.get("msg") or "invalid")
        parts.append(f"{loc}: {msg}" if loc else msg)
    return "；".join(parts) or "字段不合法"


def plan_summary(plan: EvChargingStationPlan) -> str:
    stall_n = sum(r.stalls for r in plan.parkingRows)
    charger_bits: list[str] = []
    counts: dict[str, int] = {}
    labels = {
        "dc_320kw": "320kW直流充电桩",
        "dc_160kw": "160kW直流充电桩",
        "dc_120kw": "120kW直流充电桩",
        "ac_14kw": "14kW交流充电桩",
    }
    for row in plan.parkingRows:
        if row.charger and row.charger.type != "none":
            kind = str(row.charger.type)
            counts[kind] = counts.get(kind, 0) + int(row.stalls)
    for kind, n in counts.items():
        charger_bits.append(f"{n} 台{labels.get(kind, '充电桩')}")
    if not charger_bits:
        extra = sum(
            1 for e in plan.equipment if e.type in {"dc_320kw", "dc_160kw", "dc_120kw", "ac_14kw"}
        )
        charger_bits.append(f"约 {extra} 台充电桩" if extra else "充电桩未标注")
    title = plan.titleBlock.title
    return (
        f"已生成{title}：场地 {plan.site.widthM:g}m×{plan.site.heightM:g}m，"
        f"{stall_n} 个车位，{'、'.join(charger_bits)}，"
        f"{len(plan.equipment)} 台站用设备。"
    )
