"""从所选知识库的 Excel 抽出价目行，再按名称/规格匹配。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from api.services.tenders.quote import _q
from api.services.tenders.tables import quote_role

_TWO = Decimal("0.01")
_SKIP = ("不含税合计", "含税合计", "税率", "合计", "小计", "总计", "备注")
_TOKEN = re.compile(r"[a-z0-9\u4e00-\u9fff]+", re.I)

CODE_HINTS: dict[str, tuple[str, ...]] = {
    "dc_320kw": ("320kw", "320千瓦", "400kw", "直流320", "320kW直流"),
    "dc_160kw": ("160kw", "160千瓦", "直流160", "160kW直流"),
    "dc_120kw": ("120kw", "120千瓦", "直流120", "120kW直流"),
    "ac_14kw": ("14kw", "14千瓦", "7kw", "交流桩", "交流14"),
    "box_transformer": ("箱变", "箱式变压", "变压器"),
    "ring_cabinet": ("环网柜", "环网箱"),
    "lv_cabinet": ("低压柜", "低压配电"),
    "group_host": ("群充", "主机"),
    "cable": ("电缆", "铜芯"),
    "trench": ("电缆沟", "沟槽"),
    "foundation": ("基础", "基座"),
}


@dataclass(frozen=True)
class CatalogItem:
    name: str
    spec: str
    unit: str
    unit_price: Decimal
    source: str = ""


def parse_catalog_xlsx(path: Path) -> list[CatalogItem]:
    from openpyxl import load_workbook

    wb = load_workbook(path, data_only=True, read_only=True)
    try:
        rows: list[CatalogItem] = []
        for ws in wb.worksheets:
            rows.extend(_sheet_items(ws, path.name))
        return rows
    finally:
        wb.close()


def _sheet_items(ws, source: str) -> list[CatalogItem]:
    mapping: dict[str, int] | None = None
    out: list[CatalogItem] = []
    for row in ws.iter_rows(values_only=True):
        cells = list(row or ())
        if mapping is None:
            mapping = _header_map(cells)
            continue
        item = _row_item(cells, mapping, source)
        if item is not None:
            out.append(item)
    return out


def _header_map(cells: list[object]) -> dict[str, int] | None:
    roles = [quote_role(c) for c in cells]
    if "name" not in roles or "price" not in roles:
        return None
    mapping: dict[str, int] = {}
    for i, role in enumerate(roles):
        if role and role not in mapping:
            mapping[role] = i
    return mapping


def _cell(cells: list[object], idx: int | None) -> object:
    if idx is None or idx < 0 or idx >= len(cells):
        return None
    return cells[idx]


def _txt(value: object) -> str:
    if value is None:
        return ""
    return str(value).replace("\r\n", "\n").strip()


def _row_item(cells: list[object], mapping: dict[str, int], source: str) -> CatalogItem | None:
    name = _txt(_cell(cells, mapping.get("name")))
    if not name or any(k in name for k in _SKIP):
        return None
    spec = _txt(_cell(cells, mapping.get("spec")))
    unit = _txt(_cell(cells, mapping.get("unit"))) or "项"
    price = _q(_cell(cells, mapping.get("price")))
    if price <= 0:
        return None
    return CatalogItem(name=name, spec=spec, unit=unit, unit_price=price, source=source)


def load_catalog(paths: list[Path]) -> list[CatalogItem]:
    items: list[CatalogItem] = []
    for path in paths:
        if path.suffix.lower() not in {".xlsx", ".xlsm"}:
            continue
        try:
            items.extend(parse_catalog_xlsx(path))
        except Exception:
            continue
    return items


def _norm(text: str) -> str:
    return re.sub(r"[\s\-_/／（）()【】\[\]]+", "", (text or "").lower())


def _tokens(text: str) -> set[str]:
    return {m.group(0).lower() for m in _TOKEN.finditer(text or "") if len(m.group(0)) >= 2}


def match_score(query: str, code: str, item: CatalogItem) -> float:
    blob = f"{item.name} {item.spec}"
    qn = _norm(query)
    bn = _norm(blob)
    if not qn or not bn:
        return 0.0
    score = 0.0
    if qn == bn:
        score = 1.0
    elif qn in bn or bn in qn:
        score = 0.86
    else:
        qt = _tokens(query)
        bt = _tokens(blob)
        if qt and bt:
            score = len(qt & bt) / max(len(qt), 1)
    for hint in CODE_HINTS.get((code or "").strip().lower(), ()):
        if _norm(hint) and _norm(hint) in bn:
            score = max(score, 0.78)
    return score


def pick_catalog(
    *,
    name: str,
    spec: str,
    code: str,
    catalog: list[CatalogItem],
) -> tuple[CatalogItem | None, float]:
    query = f"{name} {spec}".strip()
    best: CatalogItem | None = None
    best_s = 0.0
    for item in catalog:
        s = match_score(query, code, item)
        if s > best_s:
            best_s = s
            best = item
    if best_s < 0.42:
        return None, best_s
    return best, best_s


def money(qty: Decimal, price: Decimal) -> Decimal:
    return (qty * price).quantize(_TWO, rounding=ROUND_HALF_UP)
