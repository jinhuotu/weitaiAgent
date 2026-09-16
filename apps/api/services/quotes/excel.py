"""按确认后的明细写出报价 Excel。"""

from __future__ import annotations

import re
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from uuid import uuid4

from api.services.quotes.catalog import money
from api.services.quotes.library import quotes_output_dir
from api.services.quotes.schema import QuoteLineIn
from common.errors import AppError, ErrorCode

_TWO = Decimal("0.01")
_FILE_RE = re.compile(r"^[a-f0-9]{12}\.xlsx$", re.I)
_UNSAFE = re.compile(r'[\\/:*?"<>|\s]+')
_HEADERS = ("序号", "名称", "规格型号", "单位", "数量", "单价（元）", "合价", "备注", "来源")


def safe_download_name(project_name: str) -> str:
    stem = _UNSAFE.sub("-", (project_name or "").strip())[:40].strip("-") or "quote"
    return f"{stem}-报价单.xlsx"


def resolve_quote_file(file_name: str) -> Path:
    name = (file_name or "").strip().replace("\\", "/").split("/")[-1]
    if not _FILE_RE.match(name):
        raise AppError(ErrorCode.BAD_REQUEST, "报价文件名无效", status_code=400)
    path = (quotes_output_dir() / name).resolve()
    root = quotes_output_dir().resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise AppError(ErrorCode.NOT_FOUND, "报价文件不存在", status_code=404)
    return path


def write_quote_xlsx(
    *,
    project_name: str,
    note: str,
    tax_rate: Decimal,
    lines: list[QuoteLineIn],
) -> tuple[Path, str, Decimal, Decimal]:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

    rows = [ln for ln in lines if str(ln.name or "").strip()]
    if not rows:
        raise AppError(ErrorCode.VALIDATION, "没有报价行，无法生成", status_code=422)

    thin = Border(
        left=Side(style="thin", color="C5CDD6"),
        right=Side(style="thin", color="C5CDD6"),
        top=Side(style="thin", color="C5CDD6"),
        bottom=Side(style="thin", color="C5CDD6"),
    )
    head_fill = PatternFill("solid", fgColor="1F4E79")
    head_font = Font(color="FFFFFF", bold=True, name="微软雅黑", size=10)
    title_font = Font(bold=True, name="微软雅黑", size=16)
    body_font = Font(name="微软雅黑", size=10)
    money_fmt = "#,##0.00"

    wb = Workbook()
    ws = wb.active
    ws.title = "报价单"
    title = (project_name or "").strip() or "充电桩场地规划报价单"
    ws.merge_cells("A1:I1")
    ws["A1"] = title
    ws["A1"].font = title_font
    ws["A1"].alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 28

    ws.merge_cells("A2:I2")
    extra = (note or "").strip()
    ws["A2"] = extra if extra else "由场地规划图识别，单价来自所选知识库价目表；请人工核对后使用。"
    ws["A2"].font = Font(name="微软雅黑", size=9, color="666666")
    ws["A2"].alignment = Alignment(wrap_text=True)

    for i, h in enumerate(_HEADERS, start=1):
        cell = ws.cell(3, i, h)
        cell.fill = head_fill
        cell.font = head_font
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = thin

    total = Decimal("0")
    for i, ln in enumerate(rows, start=1):
        qty = ln.qty if ln.qty > 0 else Decimal("0")
        price = ln.unitPrice if ln.unitPrice > 0 else Decimal("0")
        amount = money(qty, price)
        total += amount
        values = [
            i,
            ln.name,
            ln.spec,
            ln.unit or "项",
            qty,
            price if price > 0 else None,
            amount if price > 0 else None,
            ln.note,
            _source_label(ln.source, ln.matchName),
        ]
        r = 3 + i
        for c, val in enumerate(values, start=1):
            cell = ws.cell(r, c, val)
            cell.font = body_font
            cell.border = thin
            cell.alignment = Alignment(vertical="center", wrap_text=c in {2, 3, 8, 9})
            if c in {6, 7} and val is not None:
                cell.number_format = money_fmt
            if c == 5:
                cell.number_format = "0.####"

    tax = (total * tax_rate).quantize(_TWO, rounding=ROUND_HALF_UP)
    inc = (total + tax).quantize(_TWO, rounding=ROUND_HALF_UP)
    foot = 4 + len(rows)
    ws.cell(foot, 1, "")
    ws.merge_cells(start_row=foot, start_column=1, end_row=foot, end_column=6)
    ws.cell(foot, 1, f"不含税合计")
    ws.cell(foot, 7, total).number_format = money_fmt
    ws.merge_cells(start_row=foot + 1, start_column=1, end_row=foot + 1, end_column=6)
    ws.cell(foot + 1, 1, f"增值税 {tax_rate * 100:.0f}%")
    ws.cell(foot + 1, 7, tax).number_format = money_fmt
    ws.merge_cells(start_row=foot + 2, start_column=1, end_row=foot + 2, end_column=6)
    ws.cell(foot + 2, 1, "含税合计")
    ws.cell(foot + 2, 7, inc).number_format = money_fmt
    for r in range(foot, foot + 3):
        ws.cell(r, 1).font = Font(name="微软雅黑", size=10, bold=True)
        ws.cell(r, 7).font = Font(name="微软雅黑", size=10, bold=True)
        ws.cell(r, 1).border = thin
        ws.cell(r, 7).border = thin

    widths = [8, 28, 36, 8, 10, 14, 14, 28, 18]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[chr(64 + i)].width = w
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToPage = True
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.print_title_rows = "1:3"

    stem = uuid4().hex[:12]
    dest = quotes_output_dir() / f"{stem}.xlsx"
    wb.save(dest)
    return dest, safe_download_name(title), total, inc


def _source_label(source: str, match_name: str) -> str:
    s = (source or "").strip()
    hit = (match_name or "").strip()
    labels = {
        "vision": "读图",
        "rule": "规则估算",
        "catalog": "价目表",
        "manual": "人工",
    }
    base = labels.get(s, s or "人工")
    if hit and s == "catalog":
        return f"{base}：{hit}"
    return base
