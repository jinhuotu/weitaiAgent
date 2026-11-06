"""Independent pagination contracts for filled forms and continuous quotations."""
import pytest
from docx import Document
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph

from api.services import bid_table_import as service

A='9985-500108478-00001'
B='9985-500108449-00001'

CASES=[
 ('analysis','货物清单单价分析表', ['序号','组件材料项目名称','产地','制造商','单位','数量','单价（含税）','合价（含税）','备注'], ['1','铜导体','河南','制造商甲','千克','10','2','20','']),
 ('technical','技术特性参数表', ['序号','名称','单位','招标人要求值','投标人保证值'], ['1.1','绝缘厚度','mm','≥4.5','4.6']),
 ('component','货物组件材料配置表', ['序号','元件名称','型式规格，参数','单位','数量','型式、规格','数量','制造商','原产地'], ['1','导体','铜','米','1','铜','1','制造商甲','河南']),
]


def flag(node,name):
    el=node.find('w:pPr/w:'+name,node.nsmap)
    return el is not None and el.get(qn('w:val'),'1') not in {'0','false','off'}


def build_source(title,headers):
    doc=Document()
    doc.add_heading('上一章节',1)
    doc.add_paragraph('上一章节正文必须保留。')
    chapter=doc.add_heading('（二）'+title,1)
    subtitle=doc.add_paragraph(title)
    subtitle.paragraph_format.page_break_before=True  # stale source flag must not orphan the heading
    doc.add_paragraph('（如有）')
    table=doc.add_table(rows=2,cols=len(headers))
    for cell,value in zip(table.rows[0].cells,headers):cell.text=value
    table.cell(1,0).text='1'
    note=table.add_row();note.cells[0].merge(note.cells[-1]).text='填写说明：保持原文。'
    doc.add_heading('下一章节',1)
    doc.add_paragraph('下一章节正文。')
    return doc,chapter,subtitle


@pytest.mark.parametrize('kind,title,headers,values',CASES)
def test_each_specification_starts_page_without_orphaning_first_chapter(tmp_path,monkeypatch,kind,title,headers,values):
    doc,_,_=build_source(title,headers)
    source_values=[list(values),list(values)]
    source_values[1][-1]='河南' if kind=='component' else values[-1]
    rows=[{'kind':kind,'code':code,'key':kind+'|'+code,'values':v} for code,v in zip([A,B],source_values)]
    monkeypatch.setattr(service,'load_import',lambda _: {'confirmed':True,'invitationId':'project','testData':False,'rows':rows})
    path=tmp_path/'filled.docx';doc.save(path)
    assert service.fill_document(path,'token','project','full')==[]
    actual=Document(path)
    chapter=next(p for p in actual.paragraphs if p.text=='（二）'+title)
    subtitle=next(p for p in actual.paragraphs if p.text==title)
    labels=[p for p in actual.paragraphs if p.text.startswith('技术规范ID：')]
    assert [p.text for p in labels]==['技术规范ID：'+A,'技术规范ID：'+B]
    assert chapter.paragraph_format.page_break_before is True
    assert chapter.paragraph_format.keep_with_next is True
    assert subtitle.paragraph_format.page_break_before is False
    assert subtitle.paragraph_format.keep_with_next is True
    assert labels[0].paragraph_format.page_break_before is False
    assert labels[1].paragraph_format.page_break_before is True
    assert all(p.paragraph_format.keep_with_next is True for p in labels)
    assert len(actual.tables)==2
    for table,expected in zip(actual.tables,source_values):
        assert [c.text for c in table.rows[1].cells]==expected
        assert '填写说明：保持原文。' in table.rows[-1].cells[0].text
        assert table.rows[0]._tr.find('w:trPr/w:tblHeader',table._tbl.nsmap) is not None
        for cell in table.rows[0].cells:
            assert cell.paragraphs[0].paragraph_format.keep_with_next is True
        for cell in table.rows[1].cells:
            assert cell.paragraphs[0].paragraph_format.keep_with_next is False
            assert cell.paragraphs[0].paragraph_format.page_break_before is False
    assert actual.paragraphs[1].text=='上一章节正文必须保留。'
    assert actual.paragraphs[-1].text=='下一章节正文。'
    assert not list(actual.element.body.iter(qn('w:br')))


def test_quotation_is_one_continuous_table_with_two_goods(tmp_path,monkeypatch):
    headers=['项目单位','项目名称','物资描述','单位','数量','未含税单价','未含税合价','税率','含税单价','含税合价']
    doc,_,_=build_source('附表1 已标价货物清单行报价暨投标报价汇总表',headers)
    values=[['项目单位甲','项目甲','铜芯电缆','千米','2','100','200','13','113','226'],['项目单位乙','项目乙','铝芯电缆','千米','3','100','300','13','113','339']]
    rows=[{'kind':'quote','code':code,'key':'quote|'+code,'values':v} for code,v in zip([A,B],values)]
    monkeypatch.setattr(service,'load_import',lambda _: {'confirmed':True,'invitationId':'project','testData':False,'rows':rows})
    path=tmp_path/'quote.docx';doc.save(path)
    assert service.fill_document(path,'token','project','full')==[]
    actual=Document(path)
    assert len(actual.tables)==1
    assert [[c.text for c in r.cells] for r in actual.tables[0].rows[1:3]]==values
    assert not any('技术规范ID：' in p.text for p in actual.paragraphs)
    assert actual.paragraphs[2].paragraph_format.page_break_before is True
    assert all(p.paragraph_format.page_break_before is False for row in actual.tables[0].rows for cell in row.cells for p in cell.paragraphs)


def test_missing_local_heading_paginates_first_label_and_leaves_prior_chapter(tmp_path,monkeypatch):
    doc=Document();head=doc.add_heading('其他章节',1);before=head._p.xml
    table=doc.add_table(rows=2,cols=5)
    for cell,value in zip(table.rows[0].cells,['序号','名称','单位','招标人要求值','投标人保证值']):cell.text=value
    table.cell(1,0).text='1'
    monkeypatch.setattr(service,'load_import',lambda _: {'confirmed':True,'invitationId':'project','testData':False,'rows':[{'kind':'technical','code':A,'values':['1','厚度','mm','4.5','4.6']}]})
    path=tmp_path/'no-local-heading.docx';doc.save(path)
    service.fill_document(path,'token','project','full')
    actual=Document(path)
    assert actual.paragraphs[0]._p.xml==before
    label=next(p for p in actual.paragraphs if p.text.startswith('技术规范ID：'))
    assert label.paragraph_format.page_break_before is True


def test_do_not_modify_unfilled_or_ambiguous_targets(tmp_path,monkeypatch):
    doc,_,_=build_source('技术特性参数表',['序号','名称','单位','招标人要求值','投标人保证值'])
    doc.add_table(rows=1,cols=5)
    for cell,value in zip(doc.tables[-1].rows[0].cells,['序号','名称','单位','招标人要求值','投标人保证值']):cell.text=value
    monkeypatch.setattr(service,'load_import',lambda _: {'confirmed':True,'invitationId':'project','testData':False,'rows':[{'kind':'technical','code':A,'values':['1','厚度','mm','4.5','4.6']}]})
    path=tmp_path/'ambiguous.docx';doc.save(path)
    before=Document(path).element.body.xml
    warnings=service.fill_document(path,'token','project','full')
    assert '无法唯一定位' in warnings[0]
    assert Document(path).element.body.xml==before


@pytest.mark.parametrize('kind,title,headers,values', CASES)
def test_source_heading_with_leading_page_break_stays_with_filled_table(tmp_path,monkeypatch,kind,title,headers,values):
    from docx.enum.text import WD_BREAK
    doc,chapter,subtitle=build_source(title,headers)
    chapter.insert_paragraph_before().add_run().add_break(WD_BREAK.PAGE)
    chapter.clear()
    chapter.add_run().add_break(WD_BREAK.PAGE)
    chapter.add_run('3.'+title+'（格式）')
    monkeypatch.setattr(service,'load_import',lambda _: {'confirmed':True,'invitationId':'project','testData':False,'rows':[{'kind':kind,'code':A,'values':values}]})
    path=tmp_path/'source-break.docx';doc.save(path)
    service.fill_document(path,'token','project','full')
    actual=Document(path)
    chapter=next(p for p in actual.paragraphs if p.text=='3.'+title+'（格式）')
    subtitle=next(p for p in actual.paragraphs if p.text==title)
    assert chapter.paragraph_format.page_break_before is True
    assert chapter.paragraph_format.keep_with_next is True
    assert not list(chapter._p.iter(qn('w:br')))
    assert subtitle.paragraph_format.page_break_before is False
    assert [c.text for c in actual.tables[0].rows[1].cells]==values


def test_final_pagination_collapses_bare_break_before_heading_and_is_idempotent():
    from api.services.cablepkg.form_pagination import normalize_duplicate_page_breaks
    doc=Document();doc.add_paragraph('上章正文');doc.add_page_break();doc.add_paragraph('')
    head=doc.add_paragraph('货物清单单价分析表');head.paragraph_format.page_break_before=True
    table=doc.add_table(rows=1,cols=1);table.cell(0,0).text='数据须保留'
    table_xml=table._tbl.xml
    assert normalize_duplicate_page_breaks(doc)==1
    assert not list(doc.element.body.iter(qn('w:br')))
    assert head.paragraph_format.page_break_before is True
    assert table._tbl.xml==table_xml
    xml=doc.element.body.xml
    assert normalize_duplicate_page_breaks(doc)==0
    assert doc.element.body.xml==xml


def test_final_pagination_keeps_source_title_and_subtitle_on_same_page():
    from api.services.cablepkg.form_pagination import normalize_duplicate_page_breaks
    doc=Document();doc.add_paragraph('上一章')
    chapter=doc.add_paragraph('3.货物组件材料配置表（格式）')
    subtitle=doc.add_paragraph('货物组件材料配置表（格式）');subtitle.paragraph_format.page_break_before=True
    assert normalize_duplicate_page_breaks(doc)==1
    assert chapter.paragraph_format.keep_with_next is True
    assert subtitle.paragraph_format.page_break_before is False
    assert [p.text for p in doc.paragraphs]==['上一章','3.货物组件材料配置表（格式）','货物组件材料配置表（格式）']


def test_final_pagination_preserves_sections_images_and_trailing_breaks():
    from api.services.cablepkg.form_pagination import normalize_duplicate_page_breaks
    from docx.enum.section import WD_SECTION
    from docx.enum.text import WD_BREAK
    from docx.oxml import OxmlElement
    doc=Document();doc.add_paragraph('原文').add_run().add_break(WD_BREAK.PAGE)
    doc.add_section(WD_SECTION.NEW_PAGE)
    image=doc.add_paragraph();image._p.append(OxmlElement('w:drawing'))
    head=doc.add_paragraph('下一章');head.paragraph_format.page_break_before=True
    before=doc.element.body.xml
    assert normalize_duplicate_page_breaks(doc)==0
    assert doc.element.body.xml==before
