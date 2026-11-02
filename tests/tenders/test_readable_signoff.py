"""Readable value fonts, natural signature flow and observable self-signing."""
from copy import deepcopy
import logging
from math import ceil
from pathlib import Path

import pytest
from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt

from api.services.tenders import document as render
from api.services.tenders.pdf_format import _fill_word_blanks, _fit_filled_run, _compact_signature_flow
from api.services.tenders.schema import BidBrief


@pytest.mark.parametrize('points',[1,7,10.5,12,14,18,24])
def test_overflowing_values_keep_the_readable_floor_and_original_size_ratio(points):
    doc=Document();p=doc.add_paragraph('投标人：')
    original=p.add_run('\t');original.underline=True;original.font.size=Pt(points)
    fixed=p.add_run('（盖单位章）');fixed.font.size=Pt(12);fixed_xml=fixed._r.xml
    b=BidBrief(bidderName='任意名称可读性验证'*20)
    before=b.model_dump()
    _fill_word_blanks(doc,b)
    floor=max(18,ceil(points*2*.75))
    assert original.font.size.pt*2>=floor
    assert original._r.rPr.find(qn('w:szCs')).get(qn('w:val'))==str(int(original.font.size.pt*2))
    assert original.text==b.bidderName and fixed._r.xml==fixed_xml and b.model_dump()==before
    assert p._p.find('w:pPr/w:wordWrap',p._p.nsmap).get(qn('w:val'))=='1'
    once=p._p.xml
    for _ in range(3):_fit_filled_run(p._p,original._r)
    assert p._p.xml==once
    assert not [br for br in p._p.iter(qn('w:br')) if br.get(qn('w:type'))=='page']


def test_font_floor_resolves_inherited_source_style():
    doc=Document();doc.styles['Normal'].font.size=Pt(10)
    style=doc.styles.add_style('SourceLarge',WD_STYLE_TYPE.PARAGRAPH);style.font.size=Pt(20)
    p=doc.add_paragraph('地址：',style=style);r=p.add_run('\t');r.underline=True
    _fill_word_blanks(doc,BidBrief(bidderAddress='任意地址'*70))
    assert r.font.size.pt>=15 and r.text=='任意地址'*70


def test_floor_first_uses_full_line_room_instead_of_recycled_blank_tabs():
    doc=Document();p=doc.add_paragraph('投标人：')
    p.paragraph_format.tab_stops.add_tab_stop(Cm(4))
    r=p.add_run('\t');r.underline=True;r.font.size=Pt(12)
    label=p.add_run('（盖单位章）');before=label._r.xml
    _fill_word_blanks(doc,BidBrief(bidderName='跨区域服务测试单位名称'))
    assert r.font.size.pt>=9
    assert label._r.xml==before
    assert not p._p.findall('w:r/w:tab',p._p.nsmap)
    # This name fits the line at normal readable size once padding is removed.
    assert r.font.size.pt==12


def test_existing_fit_text_cannot_hide_a_readable_size_value():
    doc=Document();p=doc.add_paragraph('姓名：');r=p.add_run('测试人员');r.font.size=Pt(1)
    fit=OxmlElement('w:fitText');fit.set(qn('w:val'),'20');r._r.get_or_add_rPr().append(fit)
    _fit_filled_run(p._p,r._r)
    assert r.font.size.pt>=9 and r._r.rPr.find(qn('w:fitText')) is None


def test_signature_contact_whitespace_is_compacted_without_touching_text_or_tables():
    doc=Document();p=doc.add_paragraph('投标人：测试单位（盖单位章）')
    p.paragraph_format.keep_together=p.paragraph_format.keep_with_next=True
    gaps=[doc.add_paragraph() for _ in range(5)]
    for gap in gaps:gap.paragraph_format.line_spacing=Cm(4)
    phone=doc.add_paragraph('电话：010-12345678')
    phone.paragraph_format.keep_together=phone.paragraph_format.keep_with_next=True
    table=doc.add_table(rows=1,cols=1);table.cell(0,0).text='原版表格'
    table_xml=deepcopy(table._tbl).xml
    _compact_signature_flow(doc)
    remaining=[gap for gap in gaps if gap._p.getparent() is doc.element.body]
    assert len(remaining)==1 and remaining[0].paragraph_format.line_spacing.pt<=12
    assert p.text=='投标人：测试单位（盖单位章）' and phone.text=='电话：010-12345678'
    assert not p.paragraph_format.keep_together and not p.paragraph_format.keep_with_next
    assert not phone.paragraph_format.keep_together and not phone.paragraph_format.keep_with_next
    assert deepcopy(table._tbl).xml==table_xml
    once=doc.element.xml;_compact_signature_flow(doc);assert doc.element.xml==once


def test_cached_generated_footer_spacer_is_removed_and_new_spacers_are_disabled():
    doc=Document();body=doc.add_paragraph('原版正文。');gap=doc.add_paragraph()
    render._apply_sign_spacer_paragraph(gap,8)
    field=doc.add_paragraph('法定代表人：测试人员（签字）')
    original=body._p.xml,field.text
    _compact_signature_flow(doc)
    assert gap._p.getparent() is None
    assert original==(body._p.xml,field.text)
    assert render._sign_spacer_cm(doc,sign_cm=3.2)==0
    before=len(doc.paragraphs);render._pad_signature_blocks(doc);assert len(doc.paragraphs)==before


def test_pagination_boundaries_attachment_frames_and_reference_anchors_are_preserved():
    doc=Document();doc.add_paragraph('投标人：测试单位')
    boundary=doc.add_paragraph();boundary._p.get_or_add_pPr().append(OxmlElement('w:sectPr'))
    frame=doc.add_paragraph();frame._p.get_or_add_pPr().append(OxmlElement('w:pBdr'))
    reference=doc.add_paragraph();reference.add_run()._r.append(OxmlElement('w:footnoteReference'))
    doc.add_paragraph('传真：010-87654321')
    original=[(p._p,p._p.xml) for p in [boundary,frame,reference]]
    _compact_signature_flow(doc)
    assert all(node.getparent() is doc.element.body and node.xml==xml for node,xml in original)


@pytest.mark.parametrize('label',['邮箱：','电子邮箱：','邮编：','投标人（盖章）：'])
def test_inherited_keep_chain_is_released_only_for_signature_fields(label):
    doc=Document();style=doc.styles.add_style('GroupedSource',WD_STYLE_TYPE.PARAGRAPH)
    style.paragraph_format.keep_together=style.paragraph_format.keep_with_next=True
    text=doc.add_paragraph('原版正文。',style=style);before=text._p.xml
    field=doc.add_paragraph(label+'a@example.test',style=style)
    _compact_signature_flow(doc)
    assert field.paragraph_format.keep_together is False and field.paragraph_format.keep_with_next is False
    assert text._p.xml==before


@pytest.mark.parametrize('agent',['','独立代理人'])
def test_agent_fallback_value_is_unchanged_and_diagnosed_as_information(caplog,agent):
    doc=Document();p=doc.add_paragraph('委托代理人：')
    b=BidBrief(agentName=agent,legalPersonName='法人测试人员')
    before=b.model_dump()
    with caplog.at_level(logging.INFO):notes=_fill_word_blanks(doc,b)
    assert p.text=='委托代理人：'+(agent or b.legalPersonName)
    assert b.model_dump()==before
    fallback='当前为法人亲自签署、未另设代理人'
    assert any(fallback in n for n in notes)==(not agent)
    assert (fallback in caplog.text)==(not agent)
    assert not [rec for rec in caplog.records if rec.levelno>=logging.WARNING]


@pytest.mark.parametrize('fixture',['equipment-chinese.docx','engineering-auto.docx','services-decimal.docx'])
def test_different_source_layouts_retain_body_xml_when_compacting_new_signature_fields(fixture):
    doc=Document(Path(__file__).parent/'fixtures'/fixture)
    originals=[(node,deepcopy(node).xml) for node in doc.element.body if node.tag!=qn('w:sectPr')]
    signature=doc.add_paragraph('投标人：')
    value=signature.add_run('\t');value.underline=True;value.font.size=Pt(14)
    signature.paragraph_format.keep_together=signature.paragraph_format.keep_with_next=True
    for _ in range(3):
        gap=doc.add_paragraph();gap.paragraph_format.line_spacing=Cm(3)
    phone=doc.add_paragraph('电话：');phone.add_run('\t').underline=True
    b=BidBrief(bidderName='任意主体名称可读性验证'*12,bidderPhone='010-12345678')
    _fill_word_blanks(doc,b)
    assert all(deepcopy(node).xml==xml for node,xml in originals)
    assert value.text==b.bidderName and value.font.size.pt>=10.5
    assert phone.text=='电话：'+b.bidderPhone
    assert not signature.paragraph_format.keep_with_next
    assert not [br for br in doc.element.body.iter(qn('w:br')) if br.get(qn('w:type'))=='page']
