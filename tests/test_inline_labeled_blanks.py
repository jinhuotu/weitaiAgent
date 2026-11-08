from docx import Document

from api.services.tenders.document import fill_inline_labeled_blanks
from api.services.tenders.schema import BidBrief


def _doc_line(label: str) -> Document:
    doc = Document()
    para = doc.add_paragraph(label)
    run = para.add_run("　　　　　　　　")
    run.underline = True
    return doc


def test_inline_labels_support_aliases_and_dry_run():
    doc = _doc_line("标段名称：")
    brief = BidBrief(lotName="10kV电缆")

    before = doc.paragraphs[0].text
    report = fill_inline_labeled_blanks(doc, brief, dry_run=True)
    assert report[0]["filled"] == 1
    assert doc.paragraphs[0].text == before

    report = fill_inline_labeled_blanks(doc, brief)
    assert report[0]["filled"] == 1
    assert "10kV电缆" in doc.paragraphs[0].text


def test_inline_labels_leave_blank_when_value_missing():
    doc = _doc_line("包号：")
    before = doc.paragraphs[0].text
    report = fill_inline_labeled_blanks(doc, BidBrief())
    assert report[0]["missing"] == 1
    assert doc.paragraphs[0].text == before
