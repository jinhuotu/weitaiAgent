"""Read-only semantic importer for multi-sheet cable bid workbooks."""
from __future__ import annotations
from pathlib import Path
from typing import Any
from openpyxl import load_workbook

KEYWORDS = {
    "offer": ("技术规范ID", "固化ID", "参数名称", "名称", "单位", "投标人保证值"),
    "price": ("技术规范ID", "单位"),
    "component": ("组件", "元件", "材料配置", "单价分析"),
    "deviation": ("偏差事项", "招标文件要求", "投标文件响应", "偏差说明"),
}

def _text(v: Any) -> str:
    return str(v).strip() if v is not None else ""

def _kind(headers: list[str]) -> str | None:
    s = set(headers)
    if "技术规范ID" in s or "固化ID" in s:
        if "投标人保证值" in s and ("参数名称" in s or "名称" in s) and "单位" in s:
            return "offer"
        if "单位" in s and any(any(x in h for x in ("单价", "合价", "金额")) for h in s):
            return "price"
    if any(x in "|".join(headers) for x in KEYWORDS["component"]): return "component"
    if any(x in "|".join(headers) for x in KEYWORDS["deviation"]): return "deviation"
    return None

def dry_run(path: str | Path, *, targets: list[dict] = (), price_targets: list[dict] = ()) -> dict:
    """Parse without writes; candidates are only matched by exact keys."""
    wb = load_workbook(path, read_only=True, data_only=True)
    result = {"sheets": [], "warnings": [], "counts": {"offer": 0, "price": 0, "component": 0, "deviation": 0}}
    offers = {(str(x.get("solidified_id") or x.get("technical_id") or x.get("code")), str(x.get("target") or x.get("parameter_name") or x.get("name")), str(x.get("unit") or "")) for x in targets}
    prices = {str(x.get("solidified_id") or x.get("technical_id") or x.get("code")) for x in price_targets}
    for ws in wb.worksheets:
        rows = ws.iter_rows(values_only=True); first = next(rows, ())
        headers = [_text(x) for x in first]; kind = _kind(headers)
        item = {"name": ws.title, "kind": kind, "rows": 0, "matched": 0, "unmatched": 0}
        if not kind:
            result["warnings"].append({"sheet": ws.title, "reason": "无法按表头语义识别"}); result["sheets"].append(item); continue
        idx = {h:i for i,h in enumerate(headers) if h}
        for row in rows:
            vals = list(row); item["rows"] += 1; result["counts"][kind] += 1
            if kind == "offer":
                code = _text(vals[idx.get("技术规范ID", idx.get("固化ID", -1))]) if idx.get("技术规范ID", idx.get("固化ID", -1)) >= 0 else ""
                name = _text(vals[idx.get("参数名称", idx.get("名称", -1))]) if idx.get("参数名称", idx.get("名称", -1)) >= 0 else ""
                unit = _text(vals[idx.get("单位", -1)]) if idx.get("单位", -1) >= 0 else ""
                value = vals[idx.get("投标人保证值", -1)] if idx.get("投标人保证值", -1) >= 0 else None
                ok = (code, name, unit) in offers and value not in (None, "")
            elif kind == "price":
                i = idx.get("技术规范ID", idx.get("固化ID", -1)); code = _text(vals[i]) if i >= 0 else ""
                ok = code in prices and any(vals[j] not in (None, "") for h,j in idx.items() if any(k in h for k in ("单价", "合价", "金额")))
            else: ok = True
            item["matched" if ok else "unmatched"] += 1
            if not ok: result["warnings"].append({"sheet": ws.title, "row": item["rows"]+1, "reason": "空值、未知ID或单位/键不精确"})
        result["sheets"].append(item)
    return result
