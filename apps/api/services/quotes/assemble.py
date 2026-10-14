"""把读图结果摊成报价行，补规则项，对价目。"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from api.services.quotes.catalog import (
    OFFICIAL_SCORE,
    CatalogItem,
    load_catalog,
    money,
    pick_catalog,
)
from api.services.quotes.library import stored_paths

_CHARGER = {
    "dc_320kw": ("320kW直流充电桩", "台"),
    "dc_160kw": ("160kW直流充电桩", "台"),
    "dc_120kw": ("120kW直流充电桩", "台"),
    "ac_14kw": ("14kW交流充电桩", "台"),
}
_EQUIP = {
    **{k: (v[0], v[1]) for k, v in _CHARGER.items()},
    "box_transformer": ("箱式变压器", "台"),
    "ring_cabinet": ("环网柜", "台"),
    "lv_cabinet": ("低压配电柜", "台"),
    "group_host": ("群充主机", "台"),
    "fire_hydrant": ("消火栓", "套"),
    "cable_well": ("电缆井", "座"),
    "cable": ("电缆", "米"),
    "trench": ("电缆沟", "米"),
    "foundation": ("充电桩基础", "处"),
}


def _qty(raw: object) -> Decimal:
    try:
        n = Decimal(str(raw or "0"))
    except Exception:
        return Decimal("0")
    return n if n > 0 else Decimal("0")


def _txt(raw: object) -> str:
    return str(raw or "").strip()


def empty_line(**kwargs: Any) -> dict[str, Any]:
    row = {
        "seq": "",
        "code": "",
        "name": "",
        "spec": "",
        "unit": "项",
        "qty": Decimal("0"),
        "unitPrice": Decimal("0"),
        "costPrice": Decimal("0"),
        "sellPrice": Decimal("0"),
        "amount": Decimal("0"),
        "source": "manual",
        "matchName": "",
        "matchScore": 0.0,
        "matchStatus": "none",
        "note": "",
    }
    row.update(kwargs)
    return row


def flatten_bom(data: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    warnings = [_txt(x) for x in (data.get("uncertainties") or []) if _txt(x)]
    lines: list[dict[str, Any]] = []
    charger_qty = Decimal("0")

    for row in data.get("chargers") or []:
        if not isinstance(row, dict):
            continue
        code = _txt(row.get("code")).lower()
        qty = _qty(row.get("qty"))
        if qty <= 0:
            continue
        title, unit = _CHARGER.get(code, (_txt(row.get("name")) or "充电桩", "台"))
        name = _txt(row.get("name")) or title
        charger_qty += qty
        lines.append(
            empty_line(
                code=code or "charger",
                name=name,
                spec=_txt(row.get("specHint") or row.get("spec")),
                unit=unit,
                qty=qty,
                source="vision",
                note=_txt(row.get("note")),
            )
        )

    for row in data.get("equipment") or []:
        if not isinstance(row, dict):
            continue
        code = _txt(row.get("code")).lower()
        qty = _qty(row.get("qty"))
        if qty <= 0:
            continue
        title, unit = _EQUIP.get(code, (_txt(row.get("name")) or code or "设备", "项"))
        name = _txt(row.get("name")) or title
        lines.append(
            empty_line(
                code=code,
                name=name,
                spec=_txt(row.get("specHint") or row.get("spec")),
                unit=unit,
                qty=qty,
                source="vision",
                note=_txt(row.get("note")),
            )
        )

    extras_names = []
    for row in data.get("extras") or []:
        if not isinstance(row, dict):
            continue
        name = _txt(row.get("name"))
        qty = _qty(row.get("qty"))
        if not name or qty <= 0:
            continue
        extras_names.append(name)
        lines.append(
            empty_line(
                name=name,
                spec=_txt(row.get("specHint") or row.get("spec")),
                unit=_txt(row.get("unit")) or "项",
                qty=qty,
                source="vision",
                note=_txt(row.get("note")),
            )
        )

    if charger_qty > 0 and not any("基础" in n for n in extras_names):
        lines.append(
            empty_line(
                code="foundation",
                name="充电桩基础",
                unit="处",
                qty=charger_qty,
                source="rule",
                note="按桩台数估算，请核对",
            )
        )

    if not lines:
        warnings.append("未从图中识别到充电桩或设备，请补充说明或改用更清晰的图纸")
    return lines, warnings


def apply_catalog(lines: list[dict[str, Any]], catalog: list[CatalogItem]) -> list[str]:
    notes: list[str] = []
    if not catalog:
        notes.append("所选知识库没有可用价目 Excel（需含名称、单价列），单价留空待核价")
        return notes
    unmatched = 0
    estimates = 0
    for row in lines:
        hit, score = pick_catalog(
            name=str(row.get("name") or ""),
            spec=str(row.get("spec") or ""),
            code=str(row.get("code") or ""),
            catalog=catalog,
        )
        row["matchScore"] = round(float(score or 0), 3)
        if hit is None:
            unmatched += 1
            row["matchStatus"] = "none"
            continue
        cost = hit.cost_price if hit.cost_price > 0 else hit.unit_price
        sell = hit.sell_price if hit.sell_price > 0 else hit.unit_price
        row["costPrice"] = cost
        row["sellPrice"] = sell
        row["unitPrice"] = sell
        row["amount"] = money(_qty(row.get("qty")), sell)
        row["matchName"] = hit.name
        if hit.spec:
            row["spec"] = hit.spec
        if not str(row.get("unit") or "").strip() or str(row.get("unit") or "") == "项":
            if hit.unit:
                row["unit"] = hit.unit
        extra = "；".join(x for x in (hit.category, hit.scene) if x)
        if extra:
            old = str(row.get("note") or "").strip()
            row["note"] = f"{old}；{extra}" if old else extra
        official = score >= OFFICIAL_SCORE
        row["matchStatus"] = "official" if official else "estimate"
        row["source"] = "catalog" if official else "estimate"
        if not official:
            estimates += 1
            notes.append(f"「{row['name']}」按「{hit.name}」估价（{score:.0%}），须确认后才能作为正式价")
    if unmatched:
        notes.append(f"{unmatched} 项未匹配到价目，单价留空")
    if estimates:
        notes.append(f"{estimates} 项为低分估价，请点确认或换条后再导出")
    return notes


def is_pending_price(row: dict[str, Any]) -> bool:
    st = str(row.get("matchStatus") or "").strip().lower()
    price = _qty(row.get("sellPrice")) or _qty(row.get("unitPrice")) or _qty(row.get("costPrice"))
    if price <= 0:
        return True
    return st == "estimate"


def pending_price_count(lines: list[dict[str, Any]]) -> int:
    return sum(1 for r in lines if str(r.get("name") or "").strip() and is_pending_price(r))


def number_lines(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i, row in enumerate(lines, start=1):
        item = dict(row)
        item["seq"] = str(i)
        qty = _qty(item.get("qty"))
        price = _qty(item.get("unitPrice"))
        cost = _qty(item.get("costPrice")) or price
        sell = _qty(item.get("sellPrice")) or price
        item["qty"] = qty
        item["unitPrice"] = price
        item["costPrice"] = cost
        item["sellPrice"] = sell
        item["amount"] = money(qty, price)
        out.append(item)
    return out


def catalog_from_docs(docs: list[dict]) -> tuple[list[CatalogItem], list[str], dict[str, Any]]:
    paths = stored_paths(docs)
    items, errors = load_catalog(paths)
    meta = {
        "fileCount": len(paths),
        "itemCount": len(items),
        "files": [p.name for p in paths],
        "errors": errors,
    }
    return items, errors, meta


def _dump_line(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "seq": str(row.get("seq") or ""),
        "code": str(row.get("code") or ""),
        "name": str(row.get("name") or ""),
        "spec": str(row.get("spec") or ""),
        "unit": str(row.get("unit") or "项"),
        "qty": float(row.get("qty") or 0),
        "unitPrice": float(row.get("unitPrice") or 0),
        "costPrice": float(row.get("costPrice") or 0),
        "sellPrice": float(row.get("sellPrice") or 0),
        "amount": float(row.get("amount") or 0),
        "source": str(row.get("source") or "manual"),
        "matchName": str(row.get("matchName") or ""),
        "matchScore": float(row.get("matchScore") or 0),
        "matchStatus": str(row.get("matchStatus") or "none"),
        "note": str(row.get("note") or ""),
    }


async def recognize_from_bom(
    *,
    data: dict[str, Any],
    docs: list[dict],
    project_name: str = "",
) -> dict[str, Any]:
    lines, warnings = flatten_bom(data)
    catalog, cat_err, meta = catalog_from_docs(docs)
    warnings.extend(cat_err)
    warnings.extend(apply_catalog(lines, catalog))
    lines = number_lines(lines)
    unmatched = pending_price_count(lines)
    title = (project_name or "").strip() or _txt(data.get("projectName"))
    return {
        "projectName": title,
        "lines": [_dump_line(r) for r in lines],
        "warnings": warnings,
        "catalogCount": len(catalog),
        "unmatched": unmatched,
        "catalog": meta,
    }
