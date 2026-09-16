"""AI 报价：价目解析、读图 JSON、对价、Excel。"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from openpyxl import Workbook

from api.services.quotes.assemble import flatten_bom, number_lines
from api.services.quotes.catalog import parse_catalog_xlsx, pick_catalog
from api.services.quotes.excel import resolve_quote_file, write_quote_xlsx
from api.services.quotes.schema import QuoteLineIn
from api.services.quotes.vision import extract_json
from common.errors import AppError


def _catalog_xlsx(tmp_path: Path) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.append(["充电桩价目"])
    ws.append(["序号", "设备名称", "规格型号", "单位", "单价"])
    ws.append([1, "160kW直流充电桩", "国标 160kW", "台", 48000])
    ws.append([2, "充电桩基础", "混凝土", "处", 1200])
    ws.append([3, "箱式变压器", "800kVA", "台", 86000])
    path = tmp_path / "price.xlsx"
    wb.save(path)
    return path


def test_parse_catalog_xlsx(tmp_path: Path) -> None:
    items = parse_catalog_xlsx(_catalog_xlsx(tmp_path))
    assert len(items) == 3
    assert items[0].name == "160kW直流充电桩"
    assert items[0].unit_price == Decimal("48000")


def test_pick_catalog_by_code(tmp_path: Path) -> None:
    items = parse_catalog_xlsx(_catalog_xlsx(tmp_path))
    hit, score = pick_catalog(name="直流桩", spec="", code="dc_160kw", catalog=items)
    assert hit is not None
    assert "160" in hit.name
    assert score >= 0.42


def test_flatten_bom_adds_foundation() -> None:
    lines, warns = flatten_bom(
        {
            "chargers": [{"code": "dc_160kw", "qty": 8}],
            "equipment": [{"code": "box_transformer", "qty": 1, "specHint": "800kVA"}],
            "uncertainties": ["图例略糊"],
        }
    )
    names = [r["name"] for r in lines]
    assert any("160" in n for n in names)
    assert "充电桩基础" in names
    assert any(r["qty"] == Decimal("8") and r["name"] == "充电桩基础" for r in lines)
    assert "图例略糊" in warns


def test_recognize_from_local_catalog(tmp_path: Path) -> None:
    from api.services.quotes.assemble import apply_catalog

    path = _catalog_xlsx(tmp_path)
    lines, _ = flatten_bom({"chargers": [{"code": "dc_160kw", "qty": 2}]})
    apply_catalog(lines, parse_catalog_xlsx(path))
    lines = number_lines(lines)
    pile = next(r for r in lines if "充电桩" in r["name"] and "基础" not in r["name"])
    assert pile["unitPrice"] == Decimal("48000")
    assert pile["amount"] == Decimal("96000")
    base = next(r for r in lines if r["name"] == "充电桩基础")
    assert base["unitPrice"] == Decimal("1200")


def test_extract_json_from_fence() -> None:
    data = extract_json('好的\n```json\n{"chargers":[{"code":"ac_14kw","qty":4}]}\n```\n')
    assert data["chargers"][0]["qty"] == 4


def test_write_and_resolve_xlsx(tmp_path, monkeypatch) -> None:
    from api.services.quotes import excel as excel_mod

    monkeypatch.setattr(excel_mod, "quotes_output_dir", lambda: tmp_path)
    path, name, total, inc = write_quote_xlsx(
        project_name="厂内充电桩",
        note="",
        tax_rate=Decimal("0.13"),
        lines=[
            QuoteLineIn(name="160kW直流充电桩", unit="台", qty=Decimal("2"), unitPrice=Decimal("48000")),
            QuoteLineIn(name="待核价电缆", unit="米", qty=Decimal("100"), unitPrice=Decimal("0")),
        ],
    )
    assert path.suffix == ".xlsx"
    assert path.is_file()
    assert total == Decimal("96000")
    assert inc == Decimal("108480.00")
    assert "报价单" in name
    got = resolve_quote_file(path.name)
    assert got == path


def test_resolve_quote_file_rejects_bad_name(tmp_path, monkeypatch) -> None:
    from api.services.quotes import excel as excel_mod

    monkeypatch.setattr(excel_mod, "quotes_output_dir", lambda: tmp_path)
    try:
        resolve_quote_file("../secret.xlsx")
        assert False
    except AppError as exc:
        assert exc.status_code == 400
