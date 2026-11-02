import hashlib
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from docx import Document
from docx.oxml.ns import qn
from PIL import Image

from api.services.tenders.match import attachment_requests
from api.services.tenders.section_materials import append_section_materials
from api.services.tenders.schema import PlaceholderItem,BidBrief
from api.services.tenders import library_kb as kb
from api.services.tenders.company_materials import scoped_library_media


def results(doc):
    return [n for n in doc.element.body.iter(qn('w:bookmarkStart')) if n.get(qn('w:name'),'').startswith('result_')]


@pytest.mark.parametrize('title',['招标文件','投标人须知','评标办法','合同条款','招标公告要求',
    '开工报审表','竣工验收报告','结算表','隐蔽验收报告','设计变更表','图纸会审表','进度付款报表','施工组织设计','附表一','附表六'])
def test_document_and_future_phase_items_are_not_materials(title):
    assert not attachment_requests('应附'+title+'复印件或扫描件。',source_kind='qualification')


@pytest.mark.parametrize('source',['contract','workflow','technical',''])
def test_only_authoritative_source_kinds_are_parsed(source):
    assert not attachment_requests('应附营业执照扫描件。',source_kind=source)


def test_historical_performance_evidence_is_not_a_future_phase_form():
    assert attachment_requests('应附近年已完成类似项目的竣工验收报告扫描件。',source_kind='qualification')


def test_role_variants_are_standard_keys_without_erasing_named_people():
    rows=attachment_requests('附：法定代表人的身份证复印件、法人身份证扫描件、委托代理人的身份证扫描件。',source_kind='format')
    assert {r.key for r in rows}=={'id_legal','id_agent'}
    named=attachment_requests('附：委托代理人张甲身份证扫描件、委托代理人李乙身份证扫描件。',source_kind='format')
    assert len(named)==2 and len({r.key for r in named})==2


def test_rejected_list_items_and_contract_subsections_produce_no_frames():
    doc=Document();doc.add_paragraph('资格审查资料',style='Heading 1')
    for label,title in zip('ABC',['投标人须知','施工组织设计','开工报审表']):doc.add_paragraph(label+'.'+title+'：应附复印件。')
    doc.add_paragraph('合同通用条款',style='Heading 1')
    doc.add_paragraph('资格审查资料',style='Heading 2');doc.add_paragraph('应附营业执照扫描件。')
    before=doc.element.xml;append_section_materials(doc,[],{},[])
    assert not results(doc) and doc.element.xml==before


@pytest.mark.parametrize('has_file',[False,True])
def test_credit_moves_from_stale_parent_to_dedicated_query_section(tmp_path,has_file):
    doc=Document();doc.add_paragraph('资格审查资料',style='Heading 1')
    doc.add_paragraph('应附信用查询截图。');other=doc.add_paragraph('资格审查其他材料',style='Heading 2')
    head=doc.add_paragraph('国家企业信用信息公示系统网站查询',style='Heading 2')
    table=doc.add_table(rows=1,cols=1);table.cell(0,0).text='原版查询说明'
    end=doc.add_paragraph('下一原版章节',style='Heading 1')
    original=[(b,b.xml) for b in doc.element.body]
    path=tmp_path/'query.png';Image.new('RGB',(160,90),'blue').save(path)
    slot=PlaceholderItem(key='credit',title='信用查询截图',attachToSection='资格审查资料')
    notes,_=append_section_materials(doc,[slot],{'credit':[path]} if has_file else {},['credit'])
    marks=results(doc);assert len(marks)==1
    positions={b:i for i,b in enumerate(doc.element.body)}
    assert positions[table._tbl]<positions[marks[0].getparent()]<positions[end._p]
    assert marks[0].get(qn('w:name')).endswith('_img' if has_file else '_box')
    assert all(b.xml==xml for b,xml in original)
    assert all(not p['fallback'] for p in doc._element._material_plans.values())
    assert not list(doc.element.body.iter(qn('w:pageBreakBefore')))
    once=doc.element.xml;append_section_materials(doc,[slot],{},['credit']);assert doc.element.xml==once


def test_unknown_is_not_dumped_in_original_other_qualification_bucket():
    doc=Document();doc.add_paragraph('资格审查其他材料',style='Heading 1');p=doc.add_paragraph('原版说明')
    notes,_=append_section_materials(doc,[PlaceholderItem(key='unknown',title='陌生核验凭证',attachToSection='不存在的条目')],{},['unknown'])
    assert any('兜底' in n for n in notes)
    assert any(x.text=='其他资料（待核对归属）' for x in doc.paragraphs)
    assert next(iter(doc._element._material_plans.values()))['head'] is not p._p.getprevious()


@pytest.mark.parametrize('key,titles',[('license',['营业执照','营业执照副本扫描件']),('credit',['信用截图','信用查询截图'])])
def test_equivalent_standard_material_keys_have_one_image_and_no_box(tmp_path,key,titles):
    doc=Document();doc.add_paragraph('资格审查资料',style='Heading 1');doc.add_paragraph('附：'+titles[0]+'扫描件。')
    scan=tmp_path/'proof.png';Image.new('RGB',(100,80),'blue').save(scan)
    slots=[PlaceholderItem(key=k,title=t,attachToSection='资格审查资料') for k,t in zip(['opaque',key],reversed(titles))]
    append_section_materials(doc,slots,{'opaque':[scan]},['opaque',key])
    marks=results(doc)
    assert len(marks)==1 and marks[0].get(qn('w:name'))=='result_'+hashlib.sha256(key.encode()).hexdigest()[:20]+'_img'
    assert len(list(doc.element.body.iter(qn('w:drawing'))))==1
    assert not [p for p in doc.paragraphs if p._p.find('w:pPr/w:pBdr',p._p.nsmap) is not None]


@pytest.mark.asyncio
@pytest.mark.parametrize('foreign',[False,True])
async def test_real_custom_license_file_crosses_bridge_but_foreign_owner_is_rejected(tmp_path,monkeypatch,foreign):
    company='松岑测试有限公司';parent=NS(public_id='p',parent_id='',name='营业执照副本',tags=['slot:custom_license'],summary='')
    child=NS(public_id='f',parent_id='p',name='scan',tags=['slot:custom_license','bidder:'+('异主体有限公司' if foreign else company)],summary='')
    path=tmp_path/'proof.png';Image.new('RGB',(180,120),'blue').save(path)
    monkeypatch.setattr(kb,'get_base_by_public_id',AsyncMock(return_value=NS(id=1)))
    monkeypatch.setattr(kb,'_list_docs',AsyncMock(return_value=[parent,child]))
    monkeypatch.setattr(kb,'_doc_file_path',lambda d:path if d is child else None)
    monkeypatch.setattr(kb,'_files_of',lambda p,children:[path])
    monkeypatch.setattr(kb,'list_slot_files',lambda _:[])
    slot=PlaceholderItem(key='license',title='营业执照',attachToSection='资格审查资料')
    media=await kb.resolve_attachments(None,[slot])
    media,_,_,_=await scoped_library_media(None,BidBrief(bidderName=company),media)
    doc=Document();doc.add_paragraph('资格审查资料',style='Heading 1');doc.add_paragraph('附：营业执照扫描件。')
    append_section_materials(doc,[slot],media,['license'])
    assert len(results(doc))==1 and results(doc)[0].get(qn('w:name')).endswith('_box' if foreign else '_img')


@pytest.mark.asyncio
async def test_same_company_license_cannot_be_used_as_credit_evidence(tmp_path,monkeypatch):
    path=tmp_path/'proof.png';Image.new('RGB',(30,20),'blue').save(path)
    parent=NS(public_id='p',parent_id='',name='营业执照',tags=['slot:license'],summary='')
    child=NS(public_id='f',parent_id='p',name='proof',tags=['slot:license','bidder:松岑测试有限公司'],summary='')
    monkeypatch.setattr(kb,'get_base_by_public_id',AsyncMock(return_value=NS(id=1)))
    monkeypatch.setattr(kb,'_list_docs',AsyncMock(return_value=[parent,child]))
    monkeypatch.setattr(kb,'_doc_file_path',lambda d:path if d is child else None)
    media,_,_,notes=await scoped_library_media(None,BidBrief(bidderName='松岑测试有限公司'),{'credit':[path]})
    assert media['credit']==[] and any('分类' in n for n in notes)
