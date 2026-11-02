from docx import Document
from api.services.tenders.schema import BidBrief,PlaceholderItem
from api.services.tenders.pdf_format import _fill_date_placeholders,_fill_para_blanks,_blank_text
from api.services.tenders.section_materials import append_section_materials


def test_split_date_slots_fill_numbers_without_moving_labels():
    for chunks in [['_','___年','__','__月','__','__日'],['\t','年','　','月','  ','日'],['年月日']]:
        doc=Document();p=doc.add_paragraph()
        p.paragraph_format.keep_with_next=True
        for chunk in chunks:
            run=p.add_run(chunk);run.font.name='SimSun'
        before=p._p.pPr.xml
        _fill_date_placeholders(p._p,BidBrief(bidDate='2026-10-31'))
        assert _blank_text(p._p)=='2026年10月31日'
        assert p._p.pPr.xml==before
        _fill_date_placeholders(p._p,BidBrief(bidDate='2026-10-31'))
        assert _blank_text(p._p)=='2026年10月31日'


def test_adjacent_runs_form_one_logical_name_slot():
    doc=Document();p=doc.add_paragraph('投标人：')
    for chunk in ['__','\t','　','___']:
        p.add_run(chunk)
    p.add_run('（盖章）')
    name='陌生多行业投标单位真实完整名称有限公司'
    _fill_para_blanks(p._p,BidBrief(bidderName=name))
    assert _blank_text(p._p)=='投标人：'+name+'（盖章）'
    assert 'fitText' not in p._p.xml


def test_cover_cannot_receive_material_placeholders():
    doc=Document();doc.add_paragraph('投 标 文 件',style='Heading 3')
    doc.add_paragraph('投标人：________')
    doc.add_paragraph('营业执照')
    before=doc.element.body.xml
    append_section_materials(doc,[PlaceholderItem(key='license',title='营业执照')],{'license':[]},['license'])
    assert doc.element.body.xml==before
