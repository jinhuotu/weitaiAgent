"""工程量 Excel 解析 → 报价行（复用 tenders 表头角色）。"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

from uuid import uuid4

from api.services.quotes.assemble import empty_line, number_lines
from api.services.quotes.catalog import money
from api.services.quotes.library import quotes_output_dir
from api.services.tenders.quote import parse_quote_path
from common.errors import AppError, ErrorCode


def lines_from_boq_bytes(raw: bytes, *, filename: str = "boq.xlsx") -> tuple[list[dict[str, Any]], list[str]]:
    if not raw:
        raise AppError(ErrorCode.VALIDATION, "工程量文件为空", status_code=422)
    suffix = Path(filename or "boq.xlsx").suffix.lower() or ".xlsx"
    if suffix not in {".xlsx", ".xlsm", ".csv"}:
        raise AppError(ErrorCode.VALIDATION, "工程量仅支持 xlsx / xlsm / csv", status_code=422)
    tmp = quotes_output_dir() / "_tmp_boq"
    tmp.mkdir(parents=True, exist_ok=True)
    path = tmp / f"{uuid4().hex[:12]}{suffix}"
    path.write_bytes(raw)
    try:
        sheet = parse_quote_path(path)
    finally:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
    if sheet is None or not sheet.lines:
        raise AppError(
            ErrorCode.VALIDATION,
            "未能从工程量文件解析出行（需含名称，以及数量或单价列）",
            status_code=422,
        )
    warnings: list[str] = []
    lines: list[dict[str, Any]] = []
    for ln in sheet.lines:
        qty = ln.qty if ln.qty > 0 else Decimal("0")
        price = ln.unit_price if ln.unit_price > 0 else Decimal("0")
        amount = ln.amount if ln.amount > 0 else money(qty, price)
        lines.append(
            empty_line(
                seq=ln.seq,
                name=ln.name,
                spec=ln.spec,
                unit=ln.unit or "项",
                qty=qty,
                unitPrice=price,
                costPrice=price,
                sellPrice=price,
                amount=amount,
                source="boq",
            )
        )
    if sheet.title:
        warnings.append(f"已解析清单「{sheet.title}」共 {len(lines)} 行")
    return number_lines(lines), warnings
