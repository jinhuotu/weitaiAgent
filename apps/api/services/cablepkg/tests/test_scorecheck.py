from docx import Document
from pathlib import Path

from api.services.cablepkg.scorecheck import build_score_report, check_docx
from api.services.cablepkg.scoring import normalize_scoring_rows


def test_check_docx_reports_conforming_and_partial():
    path = Path("apps/api/services/cablepkg/tests/_scorecheck_bid.docx")
    doc = Document()
    doc.add_paragraph("报告类型：型式试验报告")
    table = doc.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "试验电压"
    table.cell(0, 1).text = "35 kV"
    doc.save(path)
    try:
        criteria = normalize_scoring_rows([
        {"id": "a", "text": "报告", "conditions": [{"field": "报告类型", "operator": "eq", "value": "型式试验报告"}]},
        {"id": "b", "text": "试验", "conditions": [
            {"field": "报告类型", "operator": "eq", "value": "型式试验报告"},
            {"field": "试验电压", "operator": "ge", "value": 40},
        ]},
        ])
        result = check_docx(path, criteria)
        assert result[0].status == "conforming"
        assert result[1].status == "partial"
        assert result[0].locations
        report = build_score_report(criteria, result)
        assert report["summary"]["conforming"] == 1
        assert report["summary"]["partial"] == 1
        assert report["rows"][0]["requirement"] == "报告"
    finally:
        path.unlink(missing_ok=True)

