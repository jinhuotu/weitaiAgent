from pathlib import Path

from docx import Document
from docx.enum.text import WD_BREAK
from docx.oxml.ns import qn
import pytest

from api.services.tenders.directory_guard import directory_blocks
from api.services.tenders.schema import PlaceholderItem
from api.services.tenders.section_materials import append_section_materials


def fixture():
    doc = Document()
    doc.add_heading('第六章 投标文件格式', 1)
    title = doc.add_paragraph('投标文件格式目录')
    table = doc.add_table(rows=1, cols=4)
    for cell, value in zip(table.rows[0].cells, ['序号', '材料名称', '提交方式', '投标工具上传端口']):
        cell.text = value
    for seq, value in [('2.7', '技术服务，包括培训、安装指导等'), ('2.10', '检测检验报告'), ('2.3', '货物组件材料配置表')]:
        cells = table.add_row().cells
        for cell, text in zip(cells, [seq, value, '√', '专项投标文件端口']):
            cell.text = text
    note = doc.add_paragraph('说明：')
    explanation = doc.add_paragraph('请针对投标文件格式目录中标注“√”项分别提交；本次投标无需重复提交资格预审文件。')
    page_break = doc.add_paragraph()
    page_break.add_run().add_break(WD_BREAK.PAGE)
    blank = doc.add_paragraph()
    return doc, [title._p, table._tbl, note._p, explanation._p, page_break._p, blank._p]


def test_directory_and_explanations_untouched_only_real_chapter_receives_content():
    doc, protected = fixture()
    head = doc.add_heading('技术服务', 2)
    doc.add_paragraph('本节应附技术服务证明扫描件。')
    end = doc.add_heading('下一章节', 2)
    original = [(node, node.xml) for node in protected]
    slot = PlaceholderItem(key='service', title='技术服务证明', attachToSection='技术服务')
    _, handled = append_section_materials(doc, [slot], {}, ['service'])
    assert 'service' in handled
    assert all(node.xml == xml for node, xml in original)
    nodes = list(doc.element.body)
    boxes = [n for n in nodes if n.find('w:pPr/w:pBdr', n.nsmap) is not None]
    assert boxes
    assert all(nodes.index(head._p) < nodes.index(box) < nodes.index(end._p) for box in boxes)
    assert not any(n in directory_blocks(doc) for n in boxes)


def test_missing_real_chapter_never_falls_back_to_directory_or_end():
    doc, protected = fixture()
    doc.add_heading('商务文件', 1)
    doc.add_paragraph('此处无技术服务章节。')
    before = doc.element.body.xml
    slot = PlaceholderItem(key='service', title='技术服务证明', attachToSection='技术服务')
    notes, handled = append_section_materials(doc, [slot], {}, ['service'])
    assert not handled
    assert doc.element.body.xml == before
    assert any('未找到指定正文章节' in n for n in notes)


def test_writer_rejects_directory_table_and_explanation_without_mutation():
    from api.services.tenders.placeholders import _drop_after
    doc, protected = fixture()
    doc.add_heading('技术文件', 1)
    before = doc.element.body.xml
    slot = PlaceholderItem(key='report', title='检测检验报告')
    for anchor in protected:
        with pytest.raises(ValueError, match='目录及说明区禁止'):
            _drop_after(doc, anchor, slot, [], complete=True, at_marker=True)
        assert doc.element.body.xml == before


def test_header_only_table_detected_without_directory_title():
    doc, protected = fixture()
    protected[0].getparent().remove(protected[0])
    head = doc.add_heading('（二）专项投标文件', 1)
    blocked = directory_blocks(doc)
    assert protected[1] in blocked
    assert protected[3] in blocked
    assert head._p not in blocked


@pytest.mark.skipif(not Path('F:/人民电缆/包1_完整招标文件_28934092821237684/SL2639-招标文件第2-6章.docx').exists(), reason='本地招标样本不可用')
def test_real_sl2639_directory_notes_end_before_price_chapter():
    path = Path('F:/人民电缆/包1_完整招标文件_28934092821237684/SL2639-招标文件第2-6章.docx')
    doc = Document(path)
    blocked = directory_blocks(doc)
    text = lambda el: ''.join(n.text or '' for n in el.iter(qn('w:t')))
    assert any(text(n) == '投标文件格式目录' for n in blocked)
    assert any('是否有格式要求' in text(n) for n in blocked)
    assert any('本次投标无需重复提交' in text(n) for n in blocked)
    assert not any(text(n) == '价格文件' for n in blocked)
    assert not any(text(n) == '技术文件' for n in blocked)
