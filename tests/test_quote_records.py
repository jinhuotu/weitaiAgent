"""报价生成记录序列化（不依赖异步 DB）。"""

from __future__ import annotations

from datetime import datetime, timezone

from api.services.quotes.records import _lines_dump, _lines_load, record_to_dict
from api.services.quotes.schema import QuoteLineIn
from db.models.quote import QuoteRecord


def test_record_to_dict_marks_missing_xlsx(tmp_path, monkeypatch):
    from api.services.quotes import records as rec

    out = tmp_path / "quotes"
    out.mkdir()
    monkeypatch.setattr(rec, "quotes_output_dir", lambda: out)

    row = QuoteRecord(
        id=1,
        public_id="abc123def4567890",
        user_id=1,
        username="admin",
        project_name="某充电站",
        note="直流 160kW",
        base_id="kb01",
        base_name="报价知识库",
        tax_rate=0.13,
        total_ex_tax=48000,
        total_inc_tax=54240,
        unmatched=1,
        line_count=2,
        xlsx_file="aabbccddee01.xlsx",
        download_name="某充电站-报价单.xlsx",
        lines_json=[{"name": "直流桩", "qty": 1, "unitPrice": 48000}],
        created_at=datetime(2026, 9, 17, 7, 0, 0, tzinfo=timezone.utc),
    )
    data = record_to_dict(row)
    assert data["id"] == "abc123def4567890"
    assert data["xlsxAvailable"] is False
    assert data["lineCount"] == 2
    assert "lines" not in data

    (out / "aabbccddee01.xlsx").write_bytes(b"PK")
    data2 = record_to_dict(row, include_lines=True)
    assert data2["xlsxAvailable"] is True
    assert data2["lines"][0]["name"] == "直流桩"


def test_lines_snapshot_roundtrip():
    rows = _lines_dump(
        [
            QuoteLineIn(name="直流桩", qty=2, unitPrice=100, amount=200, source="catalog"),
            QuoteLineIn(name="", qty=1, unitPrice=1),
        ]
    )
    assert len(rows) == 1
    assert rows[0]["unitPrice"] == 100.0
    loaded = _lines_load(rows)
    assert loaded[0].name == "直流桩"
    assert float(loaded[0].qty) == 2


def test_sheet_from_quote_records_merges():
    from api.services.tenders.quote import sheet_from_quote_records

    class Row:
        def __init__(self, name, lines, tax=0.13, inc=0):
            self.project_name = name
            self.tax_rate = tax
            self.total_inc_tax = inc
            self.lines_json = lines

    sheet = sheet_from_quote_records(
        [
            Row(
                "站 A",
                [{"name": "直流桩", "qty": 1, "unitPrice": 48000, "amount": 48000, "unit": "台"}],
                inc=54240,
            ),
            Row(
                "站 B",
                [{"name": "基础", "qty": 2, "unitPrice": 1200, "amount": 2400, "unit": "处"}],
                inc=2712,
            ),
        ]
    )
    assert sheet is not None
    assert len(sheet.lines) == 2
    assert "2 份" in sheet.title
    assert float(sheet.total_ex_tax) == 50400.0
