"""造价测算：材料成本 + 费率 → 成本 / 预算 / 售价基数。"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from api.services.quotes.catalog import money
from api.services.quotes.contract import merge_rates

_TWO = Decimal("0.01")


def _q(raw: object) -> Decimal:
    try:
        return Decimal(str(raw or "0"))
    except Exception:
        return Decimal("0")


def apply_costing(
    lines: list[dict[str, Any]],
    rates_raw: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rates = merge_rates(rates_raw)
    material = Decimal("0")
    out: list[dict[str, Any]] = []
    for row in lines:
        item = dict(row)
        qty = _q(item.get("qty"))
        cost = _q(item.get("costPrice"))
        sell = _q(item.get("sellPrice"))
        unit = _q(item.get("unitPrice"))
        if cost <= 0 and unit > 0:
            cost = unit
        if sell <= 0 and unit > 0:
            sell = unit
        if sell <= 0 and cost > 0:
            sell = cost
        if cost <= 0 and sell > 0:
            cost = sell
        item["costPrice"] = cost
        item["sellPrice"] = sell
        item["unitPrice"] = sell if sell > 0 else cost
        item["amount"] = money(qty, item["unitPrice"])
        item["costAmount"] = money(qty, cost)
        material += money(qty, cost)
        out.append(item)

    labor = (material * rates["laborCoef"]).quantize(_TWO, rounding=ROUND_HALF_UP)
    base = material + labor
    measure = (base * rates["measureRate"]).quantize(_TWO, rounding=ROUND_HALF_UP)
    manage = ((base + measure) * rates["manageRate"]).quantize(_TWO, rounding=ROUND_HALF_UP)
    cost_total = (base + measure + manage).quantize(_TWO, rounding=ROUND_HALF_UP)
    profit = (cost_total * rates["profitRate"]).quantize(_TWO, rounding=ROUND_HALF_UP)
    quote_ex = (cost_total + profit).quantize(_TWO, rounding=ROUND_HALF_UP)
    contingency = (cost_total * rates["contingencyRate"]).quantize(_TWO, rounding=ROUND_HALF_UP)
    budget_ex = ((cost_total + contingency) * rates["budgetCoef"]).quantize(_TWO, rounding=ROUND_HALF_UP)
    tax_rate = rates["taxRate"]

    summary = {
        "materialCost": float(material),
        "laborCost": float(labor),
        "measureFee": float(measure),
        "manageFee": float(manage),
        "costExTax": float(cost_total),
        "profit": float(profit),
        "quoteExTax": float(quote_ex),
        "contingency": float(contingency),
        "budgetExTax": float(budget_ex),
        "taxRate": float(tax_rate),
        "costIncTax": float((cost_total * (1 + tax_rate)).quantize(_TWO, rounding=ROUND_HALF_UP)),
        "quoteIncTax": float((quote_ex * (1 + tax_rate)).quantize(_TWO, rounding=ROUND_HALF_UP)),
        "budgetIncTax": float((budget_ex * (1 + tax_rate)).quantize(_TWO, rounding=ROUND_HALF_UP)),
        "rates": {k: float(v) for k, v in rates.items()},
    }
    return out, summary
