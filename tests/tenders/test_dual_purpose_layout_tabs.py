"""A single underline tab can also separate two fixed label columns."""
from copy import deepcopy

import pytest
from docx import Document
from docx.oxml.ns import qn
from docx.shared import Cm, Pt

from api.services.tenders.pdf_format import _blank_text, _write_blank_span, _fill_word_blanks
from api.services.tenders.schema import BidBrief


@pytest.mark.parametrize('labels',[('地址：','网','址：'),('电话：','传','真：'),('Alpha:','Bet','a:'),('甲栏：','乙','栏：')])
@pytest.mark.parametrize('order',['forward','reverse'])
@pytest.mark.parametrize('fixed_underlined',[False,True])
def test_dual_purpose_tab_preserves_split_fixed_label_and_original_node(labels,order,fixed_underlined):
    doc=Document();p=doc.add_paragraph(labels[0]);p.paragraph_format.tab_stops.add_tab_stop(Cm(8))
    value=p.add_run('\t');value.underline=True;value.font.size=Pt(12)
    tab=value._r.find(qn('w:tab'))
    fixed=[p.add_run(label) for label in labels[1:]]
    for label in fixed:label.underline=fixed_underlined
    next_slot=p.add_run('\t');next_slot.underline=True;next_slot.font.size=Pt(12)
    before=[r._r.xml for r in fixed];props=deepcopy(p._p.pPr).xml
    def fill_first():
        start=len(labels[0]);_write_blank_span(p._p,start,start+1,'Value-A')
    def fill_last():
        end=len(_blank_text(p._p));_write_blank_span(p._p,end-1,end,'Value-B')
    for fill in ([fill_first,fill_last] if order=='forward' else [fill_last,fill_first]):fill()
    assert value.text=='Value-A' and value.underline
    assert next_slot.text=='Value-B' and next_slot.underline
    assert tab.getparent() is not None and tab.getparent() is not value._r
    assert tab.getparent().find('w:rPr/w:u',p._p.nsmap).get(qn('w:val'))=='none'
    assert p.text==labels[0]+'Value-A\t'+''.join(labels[1:])+'Value-B'
    assert [r._r.xml for r in fixed]==before and deepcopy(p._p.pPr).xml==props


def test_fixed_label_and_blank_can_share_one_underlined_run():
    doc=Document();p=doc.add_paragraph('Alpha:');p.paragraph_format.tab_stops.add_tab_stop(Cm(8))
    r=p.add_run('\tBeta:\t');r.underline=True
    _write_blank_span(p._p,6,7,'Value')
    assert p.text=='Alpha:Value\tBeta:\t'
    assert r.text=='Value' and r.underline


def test_column_boundary_survives_readability_floor_and_repeated_fitting():
    from api.services.tenders.pdf_format import _fit_filled_run
    doc=Document();p=doc.add_paragraph('Alpha:');p.paragraph_format.tab_stops.add_tab_stop(Cm(3))
    r=p.add_run('\t');r.underline=True;r.font.size=Pt(14)
    fixed=p.add_run('Beta:');xml=fixed._r.xml
    p.add_run('\t').underline=True
    _write_blank_span(p._p,6,7,'Long readable value '*30)
    assert r.font.size.pt>=10.5 and fixed._r.xml==xml
    assert p._p.find('w:r/w:tab',p._p.nsmap) is not None
    once=p._p.xml
    for _ in range(3):_fit_filled_run(p._p,r._r)
    assert p._p.xml==once
    assert not list(p._p.iter(qn('w:br')))


@pytest.mark.parametrize('suffix',['（盖单位章）','(annotation)','ordinary prose.'])
def test_suffix_or_prose_does_not_create_a_column_jump(suffix):
    doc=Document();p=doc.add_paragraph('Alpha:');p.paragraph_format.tab_stops.add_tab_stop(Cm(8))
    r=p.add_run('\t');r.underline=True;p.add_run(suffix)
    _write_blank_span(p._p,6,7,'Value')
    assert p.text=='Alpha:Value'+suffix
    assert not p._p.findall('w:r/w:tab',p._p.nsmap)


def test_actual_multiline_fill_retains_two_dual_purpose_column_tabs_idempotently():
    doc=Document();p=doc.add_paragraph();p.paragraph_format.tab_stops.add_tab_stop(Cm(8))
    for n,labels in enumerate([['地　址：','网　址：'],['电　话：','传　真：']]):
        if n:p.add_run().add_break()
        for label in labels:
            p.add_run(label);r=p.add_run('\t');r.underline=True;r.font.size=Pt(12)
    brief=BidBrief(bidderAddress='通用地址',bidderWebsite='https://example.test',bidderPhone='010-12345678',bidderFax='010-87654321')
    _fill_word_blanks(doc,brief)
    assert len(p._p.findall('w:r/w:tab',p._p.nsmap))==2
    assert '\t网　址：' in p.text and '\t传　真：' in p.text
    once=p._p.xml;_fill_word_blanks(doc,brief);assert p._p.xml==once


@pytest.mark.parametrize('fixture',['equipment-chinese.docx','engineering-auto.docx','services-decimal.docx'])
def test_distinct_document_structures_keep_original_body_when_filling_columns(fixture):
    from pathlib import Path
    doc=Document(Path(__file__).parent/'fixtures'/fixture)
    originals=[(el,deepcopy(el).xml) for el in doc.element.body]
    p=doc.add_paragraph('Alpha:');p.paragraph_format.tab_stops.add_tab_stop(Cm(8))
    p.add_run('\t').underline=True;p.add_run('Beta:');p.add_run('\t').underline=True
    _write_blank_span(p._p,6,7,'Value')
    assert p.text=='Alpha:Value\tBeta:\t'
    assert all(deepcopy(el).xml==xml for el,xml in originals)
