"""Real blank contact paragraphs, with tabs inside their fixed labels."""
from copy import deepcopy
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from docx.shared import Cm, Pt

from api.services.tenders.pdf_format import (
    _blank_text, _blank_tokens, _tab_role, _write_blank_span,
    _fill_word_blanks, _compact_signature_flow,
)
from api.services.tenders.schema import BidBrief


def test_real_contact_structure_keeps_labels_atomic_and_restores_field_boundaries():
    doc=Document(Path(__file__).parent/'fixtures/rendering/signoff-internal-tabs.docx')
    p=doc.paragraphs[0];tokens=list(_blank_tokens(p._p));blob=_blank_text(p._p)
    assert [_tab_role(t,blob,tokens) for t in tokens if t[1].tag==qn('w:tab')]==['label','slot','label','slot']
    labels=[(r,r.xml) for p in doc.paragraphs for r in p._p.findall(qn('w:r'))
            if ''.join(t.text or '' for t in r.iter(qn('w:t'))).strip()]
    brief=BidBrief(bidderAddress='通用地址',bidderWebsite='https://example.test',
                   bidderPhone='010-12345678',bidderFax='010-87654321')
    _fill_word_blanks(doc,brief)
    assert '通用地址\n网\t址：https://example.test' in doc.paragraphs[0].text
    assert '010-12345678\n传\t真：010-87654321' in doc.paragraphs[1].text
    assert all(r.xml==xml for r,xml in labels if r.getparent() is not None)
    for p in doc.paragraphs:
        assert all(r.font.size is None or r.font.size.pt>=9 for r in p.runs)
    once=doc.element.xml;_fill_word_blanks(doc,brief);assert doc.element.xml==once
    assert not [b for b in doc.element.body.iter(qn('w:br')) if b.get(qn('w:type'))=='page']


def test_unseen_label_text_uses_the_same_internal_tab_rules():
    doc=Document();p=doc.add_paragraph('Al');p.add_run('\t');p.add_run('pha:')
    p.paragraph_format.tab_stops.add_tab_stop(Cm(4))
    slot=p.add_run('\t');slot.underline=True
    p.add_run('Be');p.add_run('\t');p.add_run('ta:');p.add_run('\t').underline=True
    start=_blank_text(p._p).index(':')+1
    _write_blank_span(p._p,start,start+1,'Value')
    assert p.text=='Al\tpha:Value\tBe\tta:\t'


def test_only_redundant_safe_gap_before_signoff_is_compacted():
    doc=Document();body=doc.add_paragraph('原版正文结束。');original=body._p.xml
    doc.add_paragraph();doc.add_paragraph()
    sign=doc.add_paragraph('投标人：');doc.add_paragraph('电话：')
    section=deepcopy(doc.element.body.find(qn('w:sectPr')));doc.add_paragraph()._p.get_or_add_pPr().append(section)
    sect_xml=section.xml
    _compact_signature_flow(doc)
    assert len(list(body._p.itersiblings(preceding=False)))>0
    assert sign._p.getprevious().getprevious() is body._p
    assert body._p.xml==original and section.xml==sect_xml
    assert not list(doc.element.body.iter(qn('w:br')))
