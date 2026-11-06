from pathlib import Path

from docx import Document
from openpyxl import Workbook

from apps.api.services.cablepkg.document_ingest import ingest


def test_docx_keeps_paragraph_table_and_placeholders(tmp_path: Path):
    path = tmp_path / "bid.docx"
    document = Document()
    document.add_heading("二、服务组织与职责", level=1)
    document.add_paragraph("项目编号：{项目编号}")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "岗位"
    table.cell(0, 1).text = "人数"
    table.cell(1, 0).text = "电缆专业工程师"
    table.cell(1, 1).text = "（4）"
    document.save(path)

    result = ingest(path)
    assert result.file_type == "docx"
    assert any(block.kind == "heading" for block in result.blocks)
    paragraph = next(block for block in result.blocks if block.kind == "paragraph")
    assert paragraph.placeholders[0].label == "项目编号"
    row = next(block for block in result.blocks if block.kind == "table_row")
    assert row.table.headers == ("岗位", "人数")
    assert row.table.values == ("电缆专业工程师", "（4）")
    assert row.source.location == "table:1:row:2"


def test_xlsx_is_read_by_sheet_and_row_without_mutation(tmp_path: Path):
    path = tmp_path / "parameters.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "技术参数"
    sheet.append(["技术规范ID", "参数名称", "单位", "投标人保证值"])
    sheet.append(["9985-1", "导体电阻", "Ω/km", "≤0.1"])
    workbook.save(path)
    before = path.read_bytes()

    result = ingest(path)
    assert path.read_bytes() == before
    row = result.blocks[0]
    assert row.table.headers[0] == "技术规范ID"
    assert row.table.values[-1] == "≤0.1"
    assert row.source.location == "sheet:技术参数:row:2"
