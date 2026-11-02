import pytest
from copy import deepcopy
from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

from api.services.tenders.pdf_format import _fill_word_blanks
from api.services.tenders.schema import default_brief


def brief():
    b = default_brief()
    b.legalPersonName = "郭志伟"
    b.agentName = "李明"
    b.tenderNo = "ZB-2026-008"
    b.bidDate = "2026-10-30"
    b.projectName = "项目名称长值测试"
    return b


def fill(doc, b=None):
    b = b or brief()
    _fill_word_blanks(doc, b)
    xml = doc.element.xml
    _fill_word_blanks(doc, b)
    assert doc.element.xml == xml


@pytest.mark.parametrize("label,value", [("招标编号：", "ZB-2026-008"),
                                          ("法定代表人：", "郭志伟"),
                                          ("日期：", "2026年10月30日")])
@pytest.mark.parametrize("pieces", [("____", "＿", "　", "\t", "   "),
                                    ("　", "\t", "   ")])
def test_label_consumes_whole_multirun_blank(label, value, pieces):
    doc = Document()
    p = doc.add_paragraph(label)
    for piece in pieces:
        r = p.add_run(piece)
        r.underline = True
    fill(doc)
    assert p.text == label + value
    assert next(r for r in p.runs if r.text == value).underline is True
    assert p.runs[0].underline is None
    assert not list(p._p.iter(qn("w:tab")))


@pytest.mark.parametrize("hint,value", [("（姓名）", "郭志伟"),
                                        ("（项目名称）", "项目名称长值测试"),
                                        ("（委托代理人姓名）", "李明")])
def test_hint_crosses_runs_and_keeps_suffix(hint, value):
    doc = Document()
    p = doc.add_paragraph("填写：")
    p.add_run("__\t").underline = True
    p.add_run(hint[:2])
    p.add_run(hint[2:])
    p.add_run("＿　\t___").underline = True
    p.add_run("（签字或盖章）")
    fill(doc)
    assert p.text == "填写：" + value + "（签字或盖章）"
    assert next(r for r in p.runs if r.text == value).underline is True


def test_single_run_multiple_fields_do_not_underlined_label_or_eat_next_blank():
    doc = Document()
    p = doc.add_paragraph("招标编号：____ 电话：____")
    b = brief()
    b.bidderPhone = "123456"
    fill(doc, b)
    assert p.text == "招标编号：ZB-2026-008电话：123456"
    assert next(r for r in p.runs if r.text == "ZB-2026-008").underline is True
    assert p.runs[0].underline is None


def test_split_date_has_no_trailing_lines():
    doc = Document()
    p = doc.add_paragraph("日期：")
    for text in ("____", "年", "\t＿", "月", "__", "日", "____"):
        p.add_run(text).underline = True
    fill(doc)
    assert p.text == "日期：2026年10月30日"


@pytest.mark.parametrize("text", ["年 月 日", "日期：年　月　日", "项目经理：____", "签字：____"])
def test_bare_dates_unknown_and_signature_blanks_unchanged(text):
    doc = Document()
    p = doc.add_paragraph(text)
    b = brief()
    b.pmName = ""
    xml = p._p.xml
    fill(doc, b)
    if '年' in text and '月' in text and '日' in text:
        assert p.text == ('日期：' if text.startswith('日期') else '') + '2026年10月30日'
    else:
        assert p._p.xml == xml


def test_missing_value_leaves_all_runs_intact():
    doc = Document()
    p = doc.add_paragraph("招标编号：")
    p.add_run("___\t").underline = True
    p.add_run("＿　").underline = True
    b = brief()
    b.tenderNo = ""
    xml = p._p.xml
    fill(doc, b)
    assert p._p.xml == xml


def test_missing_date_does_not_replace_lines_with_space_date():
    doc = Document()
    p = doc.add_paragraph("日期：____年____月____日____")
    b = brief()
    b.bidDate = ""
    xml = p._p.xml
    fill(doc, b)
    assert p._p.xml == xml


def test_long_value_constrains_width_and_preserves_line_height_and_font():
    doc = Document()
    p = doc.add_paragraph("项目名称：")
    p.paragraph_format.line_spacing = 1.5
    r = p.add_run("________")
    r.font.name = "宋体"
    r.bold = True
    r.underline = True
    ppr = deepcopy(p._p.pPr).xml
    b = brief()
    b.projectName = "很长的项目名称" * 8
    fill(doc, b)
    value = next(r for r in p.runs if r.text == b.projectName)
    assert value.bold and value.underline and value.font.name == "宋体"
    assert value._r.rPr.find(qn("w:fitText")) is None
    actual = deepcopy(p._p.pPr)
    for wrap in list(actual.findall(qn('w:wordWrap'))):actual.remove(wrap)
    assert actual.xml == ppr


def test_hints_do_not_reach_back_across_substantive_text():
    doc = Document()
    p = doc.add_paragraph("未知项：____；另行填写")
    p.add_run("（姓名）")
    fill(doc)
    assert p.text == "未知项：____；另行填写郭志伟"


def test_long_fitted_value_does_not_relax_an_exact_table_row():
    doc = Document()
    cell = doc.add_table(rows=1, cols=1).cell(0, 0)
    cell._tc.get_or_add_tcPr().find(qn("w:tcW")).set(qn("w:w"), "3000")
    trpr = cell._tc.getparent().get_or_add_trPr()
    height = OxmlElement("w:trHeight")
    height.set(qn("w:val"), "400")
    height.set(qn("w:hRule"), "exact")
    trpr.append(height)
    p = cell.paragraphs[0]
    p.add_run("项目名称：")
    p.add_run("____").underline = True
    b = brief()
    b.projectName = "很长的项目名称" * 8
    fill(doc, b)
    assert height.get(qn("w:hRule")) == "exact"
    assert height.get(qn("w:val")) == "400"


def test_table_blank_and_separate_text_nodes_are_preserved():
    doc = Document()
    p = doc.add_table(rows=1, cols=1).cell(0, 0).paragraphs[0]
    r = p.add_run("招标编号：")
    t = OxmlElement("w:t")
    t.text = "____"
    r._r.append(t)
    r.add_tab()
    fill(doc)
    assert p.text == "招标编号：ZB-2026-008"


def test_literal_line_with_underline_disabled_still_underlines_value():
    doc = Document()
    p = doc.add_paragraph("招标编号：")
    p.add_run("____").underline = False
    fill(doc)
    assert next(r for r in p.runs if r.text == brief().tenderNo).underline is True
