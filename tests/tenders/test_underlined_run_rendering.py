"""Rendering-only regression: original runs, fixed tab columns and natural flow."""
from copy import deepcopy

import pytest
from docx import Document
from docx.oxml.ns import qn
from docx.shared import Cm, Pt

from api.services.tenders import document as render, native_format
from api.services.tenders.pdf_format import _fill_word_blanks, _write_blank_span, _blank_text
from api.services.tenders.schema import BidBrief


def brief():
    return BidBrief(tenderNo='TEST-001', bidderName='测试供应单位', legalPersonName='测试人员',
                    bidderAddress='测试地址十六号', bidderWebsite='https://example.test',
                    bidderPhone='010-12345678', bidderFax='010-87654321', bidDate='2026-11-01')


@pytest.mark.parametrize('blank', ['\t', '   ', '____', '＿　'])
def test_original_underlined_run_is_the_value_anchor(blank):
    doc = Document();p = doc.add_paragraph('投标人：')
    original = p.add_run(blank);original.underline = True;original.font.size = Pt(14)
    extra = p.add_run('___');extra.underline = True
    suffix = p.add_run('（盖单位章）');suffix.font.size = Pt(12)
    before = suffix._r.xml
    _fill_word_blanks(doc, brief())
    assert original._r.getparent() is p._p and original.text == brief().bidderName
    assert original.underline and suffix._r.xml == before
    assert extra._r.getparent() is None
    assert p.text == '投标人：' + brief().bidderName + '（盖单位章）'
    once = p._p.xml;_fill_word_blanks(doc, brief());assert p._p.xml == once


def test_legacy_appended_value_moves_to_its_underline_without_duplicate():
    doc = Document();p = doc.add_paragraph('投标人：')
    blank = p.add_run('\t');blank.underline = True
    duplicate = p.add_run(brief().bidderName)
    padding = p.add_run('___');padding.underline = True
    suffix = p.add_run('（盖单位章）');before = suffix._r.xml
    _fill_word_blanks(doc, brief())
    assert blank.text == brief().bidderName and p.text.count(brief().bidderName) == 1
    assert duplicate._r.getparent() is None and padding._r.getparent() is None
    assert suffix._r.xml == before


def test_multiline_contacts_preserve_label_runs_and_layout_tab_nodes():
    doc = Document();p = doc.add_paragraph()
    p.paragraph_format.tab_stops.add_tab_stop(Cm(8))
    fixed = [];separators = [];values = []
    for row, fields in enumerate([[('地　址：','bidderAddress'),('网　址：','bidderWebsite')],
                                  [('电　话：','bidderPhone'),('传　真：','bidderFax')]]):
        if row:p.add_run().add_break()
        for column,(label,attribute) in enumerate(fields):
            if column:
                separator = p.add_run();separator.add_tab();separators.append(separator._r[0])
            fixed.append(p.add_run(label))
            slot = p.add_run('\t');slot.underline = True;values.append((slot,attribute))
    before = [(run,run._r.xml) for run in fixed];props = p._p.pPr.xml
    _fill_word_blanks(doc,brief())
    assert all(slot.text == getattr(brief(),attribute) for slot,attribute in values)
    assert all(run._r.xml == xml for run,xml in before)
    assert p._p.pPr.xml == props
    assert all(tab.getparent() is not None for tab in separators)
    assert len(p._p.findall('w:r/w:tab',p._p.nsmap)) == 4
    assert not any(run.underline and '\t' in run.text for run in p.runs)
    once=p._p.xml;_fill_word_blanks(doc,brief());assert p._p.xml==once


def test_long_value_shrinks_only_the_fill_run_before_the_next_fixed_tab():
    doc=Document();p=doc.add_paragraph('投标人：')
    p.paragraph_format.tab_stops.add_tab_stop(Cm(9))
    p.paragraph_format.line_spacing=1.5
    value=p.add_run('\t');value.underline=True;value.font.size=Pt(14)
    separator=p.add_run();separator.add_tab()
    suffix=p.add_run('（盖单位章）');suffix.font.size=Pt(14)
    props=deepcopy(p._p.pPr).xml;suffix_xml=suffix._r.xml
    b=brief();b.bidderName='跨区域联合服务测试单位'*6
    _fill_word_blanks(doc,b)
    assert value.text==b.bidderName and 10.5 <= value.font.size.pt < 14
    actual_props=deepcopy(p._p.pPr)
    for flag in list(actual_props.findall(qn('w:wordWrap'))):actual_props.remove(flag)
    assert value.underline and suffix._r.xml==suffix_xml and actual_props.xml==props
    assert len(p._p.findall('w:r/w:tab',p._p.nsmap))<=2
    assert not render._blank_runs(p)  # Retained non-underlined tabs are alignment, not new slots.
    assert not list(p._p.iter(qn('w:br')))


def test_builtin_fill_groups_consecutive_blank_runs_and_preserves_columns():
    doc=Document();p=doc.add_paragraph('姓名：')
    a=p.add_run('\t');a.underline=True
    tail=p.add_run('　__');tail.underline=True
    separator=p.add_run();separator.add_tab()
    label=p.add_run('电话：');before=label._r.xml
    b=p.add_run('\t');b.underline=True
    assert render._fill_blanks(p,'测试人员','010-12345678')==2
    assert a.text=='测试人员' and b.text=='010-12345678'
    assert tail._r.getparent() is None and label._r.xml==before
    assert p.text=='姓名：测试人员\t电话：010-12345678'


def test_builtin_signature_reuses_original_runs_and_given_name():
    doc=Document();p=doc.add_paragraph('法定代表人：')
    slot=p.add_run('\t');slot.underline=True
    suffix=p.add_run('（签字或盖章）');before=suffix._r.xml
    p.paragraph_format.tab_stops.add_tab_stop(Cm(10));props=p._p.pPr.xml
    render._rewrite_labeled_underline(p,[('法定代表人：','测试人员','（签字或盖章）')])
    assert slot.text=='测试人员' and suffix._r.xml==before and p._p.pPr.xml==props


def test_native_cover_does_not_append_name_to_stamp_run(monkeypatch):
    doc=Document();p=doc.add_paragraph('法定代表人：')
    slot=p.add_run('\t');slot.underline=True
    stamp=p.add_run('（签字或盖章）');before=stamp._r.xml
    monkeypatch.setattr(native_format,'_cover_elements',lambda *args:[p._p])
    native_format.fill_native_cover(doc,brief())
    assert slot.text==brief().legalPersonName and slot.underline
    assert stamp._r.xml==before and p.text.count(brief().legalPersonName)==1


def test_cached_native_cover_repairs_a_value_previously_appended_to_the_stamp(monkeypatch):
    doc=Document();p=doc.add_paragraph('法定代表人：')
    original=p.add_run('\t');original.underline=True
    stamp=p.add_run(brief().legalPersonName+'（签字或盖章）');stamp.font.size=Pt(12)
    props=deepcopy(stamp._r.rPr).xml
    monkeypatch.setattr(native_format,'_cover_elements',lambda *args:[p._p])
    native_format.fill_native_cover(doc,brief())
    assert original.text==brief().legalPersonName and original.underline
    assert stamp.text=='（签字或盖章）' and deepcopy(stamp._r.rPr).xml==props
    assert p.text.count(brief().legalPersonName)==1
    once=p._p.xml;native_format.fill_native_cover(doc,brief());assert p._p.xml==once


def test_plain_cover_alignment_tab_is_not_mistaken_for_an_unfilled_underline(monkeypatch):
    doc=Document();p=doc.add_paragraph('投标人或联合体牵头人：\t（盖单位章）')
    monkeypatch.setattr(native_format,'_cover_elements',lambda *args:[p._p])
    native_format.fill_native_cover(doc,brief())
    assert p.text.count(brief().bidderName)==1
    once=p._p.xml
    native_format.fill_native_cover(doc,brief())
    assert p._p.xml==once and p.text.count(brief().bidderName)==1


def test_existing_date_keeps_year_month_day_labels_and_tab_stop_structure():
    doc=Document();p=doc.add_paragraph('日期：')
    slots=[];labels=[]
    for label in ['年','月','日']:
        slot=p.add_run('\t');slot.underline=True;slots.append(slot)
        labels.append(p.add_run(label))
    p.paragraph_format.tab_stops.add_tab_stop(Cm(7));props=p._p.pPr.xml
    fixed=[run._r.xml for run in labels]
    render._rewrite_date_line(p,'2026','11','01')
    assert p._p.pPr.xml==props and [run._r.xml for run in labels]==fixed
    assert [slot.text for slot in slots]==['2026','11','01']


def test_signature_block_can_break_without_gluing_previous_body():
    doc=Document();previous=doc.add_paragraph('原版正文。');before=previous._p.xml
    spacer=doc.add_paragraph()
    lines=[doc.add_paragraph(text) for text in ['投标人：测试供应单位（盖单位章）',
           '法定代表人：测试人员（签字或盖章）','日期：2026年11月01日']]
    for p in [spacer,*lines]:p.paragraph_format.keep_together=True;p.paragraph_format.keep_with_next=True
    render._glue_sign_off(doc,spacer,lines)
    assert previous._p.xml==before
    assert all(p.paragraph_format.keep_together is False and p.paragraph_format.keep_with_next is False for p in [spacer,*lines])
    assert not [br for br in doc.element.body.iter(qn('w:br')) if br.get(qn('w:type'))=='page']


def test_only_a_separately_split_stamp_hint_keeps_with_its_value_line():
    doc=Document();p=doc.add_paragraph('投标人：');slot=p.add_run('\t');slot.underline=True
    hint=doc.add_paragraph('（盖单位章）')
    p.paragraph_format.keep_together=True;p.paragraph_format.keep_with_next=True
    hint.paragraph_format.keep_together=True;hint.paragraph_format.keep_with_next=True
    _fill_word_blanks(doc,brief())
    assert p.paragraph_format.keep_with_next is True and p.paragraph_format.keep_together is False
    assert hint.paragraph_format.keep_with_next is False and hint.paragraph_format.keep_together is False


@pytest.mark.parametrize('remaining,added',[(1,0),(20,0)])
def test_render_padding_is_not_added_when_signature_would_overflow(monkeypatch,remaining,added):
    doc=Document();doc.add_paragraph('原版正文。')
    doc.add_paragraph('投标人：测试供应单位（盖单位章）')
    doc.add_paragraph('法定代表人：测试人员（签字或盖章）')
    doc.add_paragraph('日期：2026年11月01日')
    original=[(p._p,p._p.xml) for p in doc.paragraphs]
    monkeypatch.setattr(render,'_estimate_cm_on_current_page',lambda *a,**kw:24-remaining)
    monkeypatch.setattr(render,'_page_body_cm',lambda *a:24)
    render._pad_signature_blocks(doc)
    assert len(doc.paragraphs)==len(original)+added
    assert all(node.xml==xml for node,xml in original)
    assert not [br for br in doc.element.body.iter(qn('w:br')) if br.get(qn('w:type'))=='page']


@pytest.mark.parametrize('heading',[None,'支付条件','授权委托书'])
def test_combined_source_signature_labels_are_not_copied_to_an_extra_paragraph(heading):
    doc=Document()
    if heading:doc.add_paragraph(heading,style='Heading 1')
    p=doc.add_paragraph('投标人：');slot=p.add_run('\t');slot.underline=True
    fixed=p.add_run('（盖单位公章）法定代表人：');xml=fixed._r.xml
    p.add_run('\t').underline=True;p.add_run('（签字）')
    count=len(doc.paragraphs)
    render._fill_paragraphs(doc,brief())
    assert slot.text==brief().bidderName and fixed._r.xml==xml
    # A spacer may be inserted before an authorization signature; its label
    # must still exist exactly once in the original combined paragraph.
    assert sum('法定代表人：' in para.text for para in doc.paragraphs)==1
    assert not any(para is not p and para.text.startswith('法定代表人：') for para in doc.paragraphs)


def test_combined_agent_identity_number_is_filled_in_place_without_a_copied_line():
    doc=Document();doc.add_paragraph('授权委托书',style='Heading 1')
    company=doc.add_paragraph('投标人：');company.add_run('\t').underline=True;company.add_run('（盖单位公章）')
    agent=doc.add_paragraph('委托代理人：');agent.add_run('\t').underline=True
    label=agent.add_run('（签字）身份证号码：');xml=label._r.xml
    identity=agent.add_run('\t');identity.underline=True
    b=brief();b.agentIdNo='11010119900101001X'
    render._fill_paragraphs(doc,b)
    assert identity.text==b.agentIdNo and label._r.xml==xml
    assert sum('身份证号码：' in p.text for p in doc.paragraphs)==1
