from docx import Document
from api.services.tenders.placeholders import _locate_note
from api.services.tenders.schema import PlaceholderItem


def test_scoped_items_and_unknown_section():
    for title in ["设备检测报告", "服务能力证明", "陌生资料凭证"]:
        doc = Document()
        toc = doc.add_paragraph("一、提交材料")
        head = doc.add_paragraph("一、提交材料")
        items = [doc.add_paragraph(f"{letter}. {title}{letter}") for letter in "ABCDE"]
        doc.add_paragraph("二、其他要求")
        doc.add_paragraph(f"应提供{title}Z")
        for letter, item in zip("ABCDE", items):
            slot = PlaceholderItem(key=letter, title=title+letter, attachToSection="一、提交材料")
            anchor, _ = _locate_note(doc, slot, set(), [toc._p])
            assert anchor is item._p
        slot = PlaceholderItem(key="fallback", title="完全不同的附件", attachToSection="一、提交材料")
        assert _locate_note(doc, slot, set(), [toc._p])[0] is head._p
        slot.attachToSection = "不存在的章节"
        assert _locate_note(doc, slot, set(), [toc._p])[0] is None
