"""Local whitespace, complete identities and authorization-site regression."""
from copy import deepcopy
import hashlib

import pytest
from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt
from PIL import Image

from api.services.tenders import placeholders as ph
from api.services.tenders.section_materials import append_section_materials
from api.services.tenders.schema import PlaceholderItem


def marks(doc):
    return [mark for mark in doc.element.body.iter(qn('w:bookmarkStart'))
            if mark.get(qn('w:name'), '').startswith('result_')]


def page_breaks(doc):
    return [br for br in doc.element.body.iter(qn('w:br')) if br.get(qn('w:type')) == 'page']


@pytest.mark.parametrize('has_file', [False, True])
def test_material_reuses_reserved_whitespace_and_preserves_following_source(tmp_path, monkeypatch, has_file):
    doc = Document()
    head = doc.add_paragraph('核验凭证', style='Heading 1')
    site = doc.add_paragraph('附：核验凭证扫描件。')
    blanks = [doc.add_paragraph() for _ in range(5)]
    for paragraph in blanks:
        paragraph.paragraph_format.line_spacing = Cm(2)
    stop = doc.add_paragraph('下一原版章节', style='Heading 1')
    source = doc.add_paragraph('后续原版正文及签字位置：________')
    original = {p._p: p._p.xml for p in (head, site, stop, source)}
    original_nodes = list(doc.element.body)
    available = ph._slot_flow_cm(doc, [p._p for p in blanks], site._p, stop._p)
    image = tmp_path / 'scan.png'
    Image.new('RGB', (500, 300), 'blue').save(image)
    monkeypatch.setattr(ph, '_break_before_slot', lambda *a: pytest.fail('enough source whitespace must not force a page'))
    slot = PlaceholderItem(key='proof', title='核验凭证', attachToSection='核验凭证')
    append_section_materials(doc, [slot], {'proof': [image]} if has_file else {}, ['proof'])
    assert list(doc.element.body) == original_nodes
    assert all(p.xml == xml for p, xml in original.items())
    assert len(marks(doc)) == 1 and marks(doc)[0].getparent() in [p._p for p in blanks]
    actual = ph._slot_flow_cm(doc, [p._p for p in blanks], site._p, stop._p)
    assert actual == pytest.approx(available, abs=0.02)
    assert not page_breaks(doc)
    once = doc.element.xml
    append_section_materials(doc, [slot], {}, ['proof'])
    assert doc.element.xml == once


@pytest.mark.parametrize('remaining,expected', [(8, 0), (0.15, 1)])
def test_page_break_uses_anchor_prefix_only_when_local_capacity_is_insufficient(monkeypatch, remaining, expected):
    doc = Document()
    anchor = doc.add_paragraph('完整原版说明。')
    # Not enough for the actual frame. This must not become a blank-page trigger.
    doc.add_paragraph().paragraph_format.line_spacing = Pt(1)
    stop = doc.add_paragraph('下一原版章节', style='Heading 1')
    before = stop._p.xml
    monkeypatch.setattr(ph, '_page_body_cm', lambda d: 24)
    def used(prefix):
        assert stop._p not in list(prefix.element.body)
        assert not marks(prefix)
        return 24 - remaining
    monkeypatch.setattr(ph, '_used_on_page_cm', used)
    ph._drop_after(doc, anchor._p, PlaceholderItem(key='proof', title='核验凭证'), [], at_marker=True)
    assert len(page_breaks(doc)) == expected
    assert stop._p.xml == before and len(marks(doc)) == 1
    if expected:
        assert page_breaks(doc)[0].getparent().getparent().getnext() is marks(doc)[0].getparent()


def test_blank_scan_stops_at_table_and_section_break(monkeypatch):
    doc = Document()
    anchor = doc.add_paragraph('原说明。')
    table = doc.add_table(rows=1, cols=1)
    table.cell(0, 0).text = '原表格'
    blank = doc.add_paragraph()
    blank.paragraph_format.line_spacing = Cm(9)
    before = deepcopy(table._tbl).xml, blank._p.xml
    monkeypatch.setattr(ph, '_page_body_cm', lambda d: 24)
    monkeypatch.setattr(ph, '_used_on_page_cm', lambda d: 2)
    ph._drop_after(doc, anchor._p, PlaceholderItem(key='proof', title='核验凭证'), [], at_marker=True)
    assert deepcopy(table._tbl).xml == before[0] and blank._p.xml == before[1]
    # Direct marker insertion does not borrow whitespace across an original table.
    assert marks(doc)[0].getparent().getnext() is table._tbl
    boundary = doc.add_paragraph()
    boundary._p.get_or_add_pPr().append(OxmlElement('w:sectPr'))
    assert not ph._slot_blank(doc, boundary._p)


@pytest.mark.parametrize('budget', [None, {'left': 1}, {'left': 2}])
def test_same_size_identity_faces_are_both_rendered_without_inherited_clipping(tmp_path, budget):
    doc = Document()
    style = doc.styles['Normal'].paragraph_format
    style.first_line_indent = Cm(2)
    style.line_spacing = Pt(12)
    files = []
    for side, color in [('front', 'blue'), ('back', 'red')]:
        path = tmp_path / (side + '.png')
        Image.new('RGB', (856, 540), color).save(path)
        files.append(path)
    assert ph._insert_slot_media(doc, files, compact_pair=True, budget=budget) == 2
    paragraph = next(p for p in doc.paragraphs if list(p._p.iter(qn('w:drawing'))))
    drawings = list(paragraph._p.iter(qn('a:blip')))
    assert len(drawings) == 2
    assert len({drawing.get(qn('r:embed')) for drawing in drawings}) == 2
    assert paragraph.paragraph_format.first_line_indent == 0
    assert paragraph.paragraph_format.line_spacing == 1.0
    assert ph._drawing_cm(paragraph._p) <= 5.4


@pytest.mark.parametrize('enabled', [None, False, True])
@pytest.mark.parametrize('has_file', [False, True])
def test_authorization_identity_site_has_one_result_even_with_missing_or_stale_catalog(tmp_path, enabled, has_file):
    doc = Document()
    doc.add_paragraph('法定代表人身份证明', style='Heading 1')
    doc.add_paragraph('附：法定代表人身份证复印件。')
    doc.add_paragraph('授权委托书', style='Heading 1')
    site = doc.add_paragraph('附：法定代表人或委托代理人的身份证复印件或扫描件。')
    stop = doc.add_paragraph('下一原版章节', style='Heading 1')
    image = tmp_path / 'agent.png'
    Image.new('RGB', (856, 540), 'red').save(image)
    # A stale binding must not place the agent result in the legal-person form.
    slots = ([PlaceholderItem(key='id_agent', title='委托代理人身份证', attachToSection='法定代表人身份证明')]
             if enabled else [])
    append_section_materials(doc, slots, {'id_agent': [image]} if has_file else {}, [s.key for s in slots], auth_enabled=enabled)
    prefix = 'result_' + hashlib.sha256(b'id_agent').hexdigest()[:20]
    agent = [mark for mark in marks(doc) if mark.get(qn('w:name')).startswith(prefix)]
    assert len(agent) == 1 and agent[0].get(qn('w:name')).endswith('_img' if has_file else '_box')
    nodes = list(doc.element.body)
    assert nodes.index(site._p) < nodes.index(agent[0].getparent()) < nodes.index(stop._p)
    assert not any('所需材料：授权委托书' in p.text for p in doc.paragraphs)


def test_optional_agent_attachment_still_respects_disabled_authorization():
    doc = Document()
    doc.add_paragraph('授权委托书', style='Heading 1')
    doc.add_paragraph('附：如有委托代理人，应附委托代理人身份证复印件。')
    slot = PlaceholderItem(key='id_agent', title='委托代理人身份证', attachToSection='授权委托书')
    append_section_materials(doc, [slot], {}, ['id_agent'], auth_enabled=False)
    assert not marks(doc)


def test_textless_reference_and_original_border_are_not_reusable_blanks():
    doc = Document()
    footnote = doc.add_paragraph()
    footnote.add_run()._r.append(OxmlElement('w:footnoteReference'))
    border = doc.add_paragraph()
    border._p.get_or_add_pPr().append(OxmlElement('w:pBdr'))
    assert not ph._slot_blank(doc, footnote._p)
    assert not ph._slot_blank(doc, border._p)


@pytest.mark.parametrize('kind', ['section', 'page'])
@pytest.mark.parametrize('planned', [False, True])
def test_empty_ending_boundary_stays_after_material_and_never_forces_next_section(monkeypatch, kind, planned):
    doc = Document()
    doc.add_paragraph('核验凭证', style='Heading 1')
    text = doc.add_paragraph('本章节完整原版说明。')
    boundary = doc.add_paragraph()
    if kind == 'section':
        boundary._p.get_or_add_pPr().append(OxmlElement('w:sectPr'))
    else:
        boundary.add_run().add_break(__import__('docx.enum.text', fromlist=['WD_BREAK']).WD_BREAK.PAGE)
    stop = doc.add_paragraph('下一原版章节', style='Heading 1')
    before = text._p.xml, boundary._p.xml, stop._p.xml
    def used(prefix):
        assert boundary._p not in list(prefix.element.body)
        return 3
    monkeypatch.setattr(ph, '_used_on_page_cm', used)
    ph._drop_after(doc, boundary._p, PlaceholderItem(key='proof', title='核验凭证'), [], at_marker=planned)
    nodes = list(doc.element.body)
    assert nodes.index(text._p) < nodes.index(marks(doc)[0].getparent()) < nodes.index(boundary._p) < nodes.index(stop._p)
    assert before == (text._p.xml, boundary._p.xml, stop._p.xml)
    assert len(page_breaks(doc)) == (1 if kind == 'page' else 0)
