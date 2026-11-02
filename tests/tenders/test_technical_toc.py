from copy import deepcopy
import re
from pathlib import Path

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph
import pytest

from api.services.tenders.schema import BidBrief, DocumentFormat
from api.services.tenders.document import _bookmark_paragraph, _write_toc_line
from api.services.tenders.technical_toc import sync, _regions, _instruction
from api.services.tenders.assemble import _fill_toc_pages


def directory(doc, entries):
    title = doc.add_paragraph('Index')
    def field(host, kind):
        run = OxmlElement('w:r')
        el = OxmlElement('w:instrText' if kind == 'instruction' else 'w:fldChar')
        if kind == 'instruction':
            el.text = ' TOC \\o "1-3" \\h '
        else:
            el.set(qn('w:fldCharType'), kind)
        run.append(el)
        host._p.append(run)
    for kind in ['begin', 'instruction', 'separate']:
        field(title, kind)
    row = title
    for text, name in entries:
        row = doc.add_paragraph()
        _write_toc_line(row, 0, text, name, '9', label='')
    field(row, 'end')
    return title


def assert_toc(doc, expected):
    regions = _regions(doc)
    assert len(regions) == 1
    rows = [n for n in regions[0] if 'PAGEREF' in _instruction(n)]
    assert [Paragraph(n, doc).text.split('\t')[0] for n in rows] == expected
    names = {m.get(qn('w:name')) for m in doc.element.iter(qn('w:bookmarkStart'))}
    for row in rows:
        assert set(re.findall(r'PAGEREF\s+([^\s\\]+)', _instruction(row))) <= names
        assert re.search(r'\t\d+$', Paragraph(row, doc).text)
    tc = [n for n in doc.element.iter(qn('w:fldSimple')) if re.search(r'\bTC\b', n.get(qn('w:instr'), ''))]
    assert len(tc) == len(expected)


@pytest.mark.parametrize('anonymous', [False, True])
def test_body_projection_unique_field_pagination_and_repeat(anonymous):
    doc = Document()
    doc.add_heading('Cover', 1)
    directory(doc, [('Missing chapter', 'absent')])
    directory(doc, [('Old label', 'source_a'), ('Old child', 'source_b')])
    stray = doc.add_paragraph()
    _write_toc_line(stray, 0, 'Stale row outside the field', 'source_b', '9', label='')
    heads = [doc.add_heading('Phase A', 1), doc.add_heading('Phase B', 2), doc.add_heading('Phase C', 1)]
    _bookmark_paragraph(heads[0], 'source_a')
    _bookmark_paragraph(heads[1], 'source_b')
    properties = [deepcopy(p._p.pPr).xml for p in heads]
    b = BidBrief(techAnonymous=anonymous)
    sync(doc, b)
    _fill_toc_pages(doc, DocumentFormat(pageNumberStart='body'), subset=True)
    assert_toc(doc, ['Phase A', 'Phase B', 'Phase C'])
    assert heads[0].paragraph_format.page_break_before
    assert [deepcopy(p._p.pPr).xml for p in heads[1:]] == properties[1:]
    assert not list(doc.element.iter(qn('w:br')))
    assert doc.settings.element.find(qn('w:updateFields')).get(qn('w:val')) == 'true'
    xml = doc.element.xml
    sync(doc, b)
    _fill_toc_pages(doc, DocumentFormat(pageNumberStart='body'), subset=True)
    assert doc.element.xml == xml
    heads[1].text = 'Renamed child'
    heads[2]._p.getparent().remove(heads[2]._p)
    doc.add_heading('Added child', 2)
    sync(doc, b)
    _fill_toc_pages(doc, DocumentFormat(pageNumberStart='body'), subset=True)
    assert_toc(doc, ['Phase A', 'Renamed child', 'Added child'])


@pytest.mark.parametrize('different', [False, True])
def test_duplicate_section_settings_are_removed_only_when_equal(different):
    doc = Document()
    directory(doc, [('Old', 'missing')])
    row = doc.paragraphs[-1]
    props = row._p.get_or_add_pPr()
    section = deepcopy(doc.element.body.sectPr)
    if different:
        section.find(qn('w:pgSz')).set(qn('w:w'), '15000')
    props.append(section)
    directory(doc, [('Actual', 'target')])
    head = doc.add_heading('Actual', 1)
    _bookmark_paragraph(head, 'target')
    sync(doc, BidBrief())
    assert len(list(doc.element.iter(qn('w:sectPr')))) == (2 if different else 1)
    assert_toc(doc, ['Actual'])


def test_anonymous_toc_scrubs_identity_without_rewriting_body():
    doc = Document()
    directory(doc, [('Outdated', 'target')])
    h = doc.add_heading('投标主体测试有限公司实施计划', 1)
    _bookmark_paragraph(h, 'target')
    original = h.text
    sync(doc, BidBrief(techAnonymous=True, bidderName='投标主体测试有限公司'))
    region = _regions(doc)[0]
    assert '投标主体测试有限公司' not in ''.join(Paragraph(n, doc).text for n in region)
    assert h.text == original


def test_simple_field_content_control_cache_is_not_retained_as_body():
    doc = Document()
    sdt = OxmlElement('w:sdt')
    content = OxmlElement('w:sdtContent')
    sdt.append(content)
    p = OxmlElement('w:p')
    simple = OxmlElement('w:fldSimple')
    simple.set(qn('w:instr'), ' TOC \\o "1-3" ')
    p.append(simple)
    content.append(p)
    doc.element.body.insert(0, sdt)
    doc.add_heading('Dynamic scope', 1)
    sync(doc, BidBrief())
    assert not list(doc.element.iter(qn('w:sdt')))
    assert_toc(doc, ['Dynamic scope'])


@pytest.mark.parametrize('source', sorted((Path(__file__).parent / 'fixtures/technical').glob('*.docx')))
def test_unfamiliar_sources_project_all_source_points_into_one_field(source):
    from api.services.tenders.technical_package import structure
    doc = Document(source)
    b = BidBrief()
    b.technicalPackage = structure(b, doc)
    source_points = [p['title'] for p in b.technicalPackage['sections']]
    assert source_points
    before = [p.text for p in doc.paragraphs]
    sync(doc, b)
    _fill_toc_pages(doc, DocumentFormat(pageNumberStart='body'), subset=True)
    region = _regions(doc)
    assert len(region) == 1
    rows = [Paragraph(n, doc).text.split('\t')[0] for n in region[0] if 'PAGEREF' in _instruction(n)]
    assert all(any(row.endswith(title) for row in rows) for title in source_points)
    # Every original paragraph survives, in the same physical order.
    body_texts = [p.text for p in doc.paragraphs if p._p not in region[0]]
    iterator = iter(body_texts)
    assert all(any(value == original for value in iterator) for original in before)
