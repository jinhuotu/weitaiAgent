import asyncio
from types import SimpleNamespace

import pytest
from docx import Document
from docx.enum.section import WD_SECTION
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

from api.services.tenders import qa, records
from api.services.tenders.assemble import assemble_bid_docx
from api.services.tenders.classifier import apply_volume_binding
from api.services.tenders.generate import generate_pair
from api.services.tenders.schema import BidBrief, OutlineItem
from db.models.tender import TenderRecord


def test_full_report_stores_separately_and_checks_both_pools(tmp_path, monkeypatch):
    monkeypatch.setattr(qa, 'tenders_output_dir', lambda: tmp_path)
    pid = 'abc123def4567890'
    for volume in ('business', 'technical', 'full'):
        qa.save_qa_report(pid, {'similarityScore': 90, 'checkedAt': 1}, volume=volume)
        assert qa.load_qa_report(pid, volume=volume)['volume'] == volume
    assert qa.normalize_qa_volume(' FULL ') == 'full'
    qa.unlink_qa_report(pid, volume='full')
    assert qa.load_qa_report(pid, volume='full') is None
    assert qa.load_qa_report(pid, volume='business')
    brief = BidBrief(outlineItems=[
        OutlineItem(id='letter', title='投标函', kind='letter', required=True),
        OutlineItem(id='plan', title='施工组织设计', kind='tech_plan', required=True),
    ])
    gaps = qa.rule_inspect(brief, '项目正文', volume='full')['missing']
    titles = {g['title'] for g in gaps}
    assert any('商务偏离表' in title for title in titles)
    assert any('技术方案' in title for title in titles)


@pytest.mark.parametrize('requested', [None, 'business', 'technical', 'full'])
def test_full_record_qa_always_targets_one_full_doc(requested):
    brief = BidBrief(generateVolume='full')
    row = SimpleNamespace(docx_file='full.docx', tech_docx_file='old-tech.docx')
    assert records._record_qa_volume(brief, requested) == 'full'
    assert records._qa_generated_file(row, brief, requested) == 'full.docx'


def test_nonfull_qa_fallback_kept():
    row = SimpleNamespace(docx_file='', tech_docx_file='tech.docx')
    assert records._qa_generated_file(row, BidBrief()) == 'tech.docx'


def test_full_regeneration_clears_previous_technical_file_reference():
    row = TenderRecord(id=1, public_id='abc123def4567890', user_id=1, username='a',
                       tech_docx_file='old-tech.docx', tech_download_name='技术标.docx')
    records._apply_generated_files(row, BidBrief(generateVolume='full'),
                                   {'docxFile': 'full.docx', 'downloadName': '综合标.docx'})
    assert row.docx_file == 'full.docx'
    assert row.tech_docx_file is None
    assert row.tech_download_name is None


def test_manual_split_slices_even_when_binding_advises_full(tmp_path, monkeypatch):
    seen = []

    def fake_generate(brief, **kwargs):
        seen.append((brief.generateVolume, brief.splitSlice))
        return {}

    monkeypatch.setattr('api.services.tenders.generate.generate_bid', fake_generate)
    generate_pair(BidBrief(volumeAdvice='full', generateBoth=True, splitSlice=True))
    assert seen == [('business', True), ('technical', False)]


@pytest.mark.parametrize('volume', ['full', 'business', 'technical'])
def test_explicit_volume_survives_binding_judgment(volume):
    result = apply_volume_binding(BidBrief(generateVolume=volume),
                                 '商务标和技术标分别装订', keep_volume=True)
    assert result.generateVolume == volume


def test_full_assemble_finalizes_both_drafts_toc_and_section_pages(tmp_path, monkeypatch):
    """原版格式章保留表格、字体、章节顺序；后填正文后重算目录。"""
    src = tmp_path / 'source.docx'
    doc = Document()
    doc.add_paragraph('商务标')
    doc.add_section(WD_SECTION.NEW_PAGE)
    doc.add_paragraph('目录')
    doc.add_paragraph('商务标')
    doc.add_paragraph('一、商务响应')
    doc.add_paragraph('技术标')
    doc.add_paragraph('二、施工组织设计')
    doc.add_section(WD_SECTION.NEW_PAGE)
    doc.add_paragraph('一、商务响应')
    doc.add_paragraph('商务原文')
    doc.add_table(rows=1, cols=1).cell(0, 0).text = '报价原表不改'
    doc.add_section(WD_SECTION.NEW_PAGE)
    doc.add_paragraph('技术标')
    doc.add_paragraph('二、施工组织设计')
    doc.add_paragraph('技术原文')
    for sec in doc.sections[2:]:
        num = OxmlElement('w:pgNumType')
        num.set(qn('w:start'), '1')
        sec._sectPr.append(num)
    doc.save(src)
    monkeypatch.setattr('api.services.tenders.pdf_format._word_format_file', lambda _: src)
    brief = BidBrief(layoutMode='outline', invitationId='abc123def456', generateVolume='full',
                     autoDraftBizResp=True, autoDraftTechPlan=True, projectName='本项目',
                     attachQualifications=False)
    calls = []

    def fill(path, brief, label):
        calls.append(label)
        document = Document(path)
        document.add_paragraph(label)
        document.save(path)
        return 1, []

    monkeypatch.setattr('api.services.tenders.assemble._fill_drafted_format_sections',
                        lambda path, brief: fill(path, brief, '已起草技术正文'))
    monkeypatch.setattr('api.services.tenders.assemble._fill_business_format_sections',
                        lambda path, brief: fill(path, brief, '已起草商务正文'))
    path, _ = assemble_bid_docx(brief, tmp_path / 'full.docx', volume='full',
                                catalog_slots=[], catalog_media={}, catalog_facts=[])
    out = Document(path)
    text = '\n'.join(p.text for p in out.paragraphs)
    assert calls == ['已起草技术正文', '已起草商务正文']
    assert '已起草商务正文' in text and '已起草技术正文' in text
    assert '商务标' not in text and '技术标' not in text
    assert '投标文件' in text
    assert out.tables[0].cell(0, 0).text == '报价原表不改'
    starts = [i for i, sec in enumerate(out.sections)
              if any(n.get(qn('w:start')) is not None for n in sec._sectPr.findall(qn('w:pgNumType')))]
    assert starts == [2]
    refs = [p for p in out.paragraphs if 'PAGEREF' in p._p.xml]
    assert len(refs) == 2
    assert all(p.text.rsplit('\t', 1)[-1].isdigit() for p in refs)
    names = [n.get(qn('w:name')) for n in out.element.body.iter(qn('w:bookmarkStart'))]
    assert len(names) == len(set(names))
    assert all(sec.header.paragraphs[0].text == '本项目' for sec in out.sections)


def test_full_saved_report_ignores_previous_single_volume_report(tmp_path, monkeypatch):
    monkeypatch.setattr(qa, 'tenders_output_dir', lambda: tmp_path)
    pid = 'abc123def4567890'
    qa.save_qa_report(pid, {'similarityScore': 70, 'checkedAt': 20}, volume='technical')
    qa.save_qa_report(pid, {'similarityScore': 99, 'checkedAt': 10}, volume='full')
    row = SimpleNamespace(public_id=pid, docx_file='full.docx', tech_docx_file='old.docx',
                          brief_json={'generateVolume': 'full'})

    async def get_row(*args):
        return row

    monkeypatch.setattr(records, '_get_row', get_row)
    for volume in (None, 'business', 'technical', 'full'):
        report = asyncio.run(records.get_record_qa(None, pid, volume=volume))['report']
        assert report['volume'] == 'full'
        assert report['similarityScore'] == 99
