"""Fixed labels following a filled field and parenthetical stamp stay whole."""
from pathlib import Path

import pytest
from docx import Document
from docx.oxml.ns import qn
from docx.shared import Cm,Pt

from api.services.tenders.pdf_format import _fill_word_blanks,_write_blank_span
from api.services.tenders.schema import BidBrief


@pytest.mark.parametrize('index',[0,1,2])
def test_real_combined_signature_formats_wrap_at_complete_label(index):
    source=Document(Path(__file__).parent/'fixtures/rendering/combined-signature-labels.docx')
    p=source.paragraphs[index]
    for other in list(source.paragraphs):
        if other._p is not p._p:other._p.getparent().remove(other._p)
    value='通用联合技术服务测试有限公司'
    original=''.join(p.text.split())
    b=BidBrief(bidderName=value)
    _fill_word_blanks(source,b)
    assert '\n法定代表人或其委托代理人：' in p.text
    assert ''.join(p.text.split())==original.replace('投标人：','投标人：'+value,1)
    assert sum(br.get(qn('w:type'))=='page' for br in p._p.iter(qn('w:br')))==0
    filled=next(r for r in p.runs if r.text==value)
    assert filled.underline and filled.font.size.pt>=9
    once=p._p.xml;_fill_word_blanks(source,b);assert p._p.xml==once


@pytest.mark.parametrize('shared',[False,True])
def test_unseen_labels_keep_font_and_move_as_a_unit_after_annotation(shared):
    doc=Document();p=doc.add_paragraph('Field A:')
    p.paragraph_format.right_indent=Cm(7)
    r=p.add_run('\t');r.underline=True;r.font.size=Pt(12)
    if shared:
        label=p.add_run('(annotation) Field B with long label:')
    else:
        p.add_run('(annotation) ');label=p.add_run('Field B with long label:')
    label.font.size=Pt(12)
    p.add_run('\t').underline=True
    _write_blank_span(p._p,8,9,'Readable field value')
    assert '\nField B with long label:' in p.text
    assert all(run.font.size is None or run.font.size.pt==12 for run in p.runs if 'Field B' in run.text)


def test_sufficient_room_preserves_original_inline_labels():
    doc=Document();p=doc.add_paragraph('A:');p.add_run('\t').underline=True
    label=p.add_run('(note) B:');before=label._r.xml;p.add_run('\t').underline=True
    _write_blank_span(p._p,2,3,'X')
    assert label._r.xml==before and not list(p._p.iter(qn('w:br')))
