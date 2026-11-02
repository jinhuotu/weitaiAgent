from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from docx import Document
from docx.oxml.ns import qn
from PIL import Image

from api.services.tenders.section_materials import append_section_materials
from api.services.tenders.schema import PlaceholderItem,BidBrief
from api.services.tenders.facts import dump_facts
from api.services.tenders.company_materials import scoped_library_media,document_matches_company
from api.services.tenders import library_kb as kb


@pytest.mark.parametrize('continuation',['的复印件或扫描件。','或提供其他有效凭证。','and its supporting copy.'])
def test_sentence_group_is_contiguous_and_source_xml_unchanged(continuation):
    doc=Document();doc.add_paragraph('保证资料',style='Heading 1')
    first=doc.add_paragraph('附：汇款凭证')
    second=doc.add_paragraph(continuation)
    doc.add_paragraph('下一独立说明。')
    original=[(b,b.xml) for b in doc.element.body]
    append_section_materials(doc,[PlaceholderItem(key='proof',title='汇款凭证',attachToSection='保证资料')],{},['proof'])
    assert first._p.getnext() is second._p
    cursor=second._p.getnext()
    while cursor is not None and cursor.find('w:pPr/w:pBdr',cursor.nsmap) is None:cursor=cursor.getnext()
    assert cursor is not None and '所需材料' in ''.join(t.text or '' for t in cursor.iter(qn('w:t')))
    assert all(b.xml==xml for b,xml in original)


def test_format_deliverable_has_one_result_below_table():
    doc=Document();doc.add_paragraph('已标价的工程量清单',style='Heading 1')
    doc.add_paragraph('请按附件格式填写并提交本章节文件。')
    table=doc.add_table(rows=2,cols=2);table.cell(0,0).text='序号';table.cell(0,1).text='报价内容'
    stop=doc.add_paragraph('下一原版章节',style='Heading 1')
    original=[(b,b.xml) for b in doc.element.body]
    append_section_materials(doc,[],{},[])
    marks=[b for b in doc.element.body.iter(qn('w:bookmarkStart')) if b.get(qn('w:name'),'').startswith('result_')]
    assert len(marks)==1 and marks[0].get(qn('w:name')).endswith('_box')
    positions={b:i for i,b in enumerate(doc.element.body)}
    assert positions[table._tbl]<positions[marks[0].getparent()]<positions[stop._p]
    assert not list(doc.element.body.iter(qn('w:pageBreakBefore')))
    assert all(b.xml==xml for b,xml in original)


@pytest.mark.asyncio
@pytest.mark.parametrize('foreign',[False,True])
async def test_child_uses_parent_key_and_verified_owner_without_overriding_ocr(tmp_path,monkeypatch,foreign):
    path=tmp_path/'license.png';Image.new('RGB',(100,100),'blue').save(path)
    parent=NS(public_id='parent',tags=['slot:license','bidder:青岑测试有限公司'],summary='',parent_id='',file_key=None,storage_path=None)
    facts=dump_facts({'role':'company','fields':[{'name':'名称','value':'外部主体有限公司','conf':1}]}) if foreign else ''
    child=NS(public_id='child',tags=['slot:id_legal'],summary=facts,parent_id='parent',file_key='f',storage_path='f')
    monkeypatch.setattr(kb,'get_base_by_public_id',AsyncMock(return_value=NS(id=1)))
    monkeypatch.setattr(kb,'_list_docs',AsyncMock(return_value=[parent,child]))
    monkeypatch.setattr(kb,'_doc_file_path',lambda d:path if d is child else None)
    brief=BidBrief(bidderName='青岑测试有限公司')
    media,allowed,_,_=await scoped_library_media(None,brief,{'license':[path]})
    assert bool(media['license']) is (not foreign)
    if not foreign:
        doc=Document();doc.add_paragraph('资格资料',style='Heading 1');doc.add_paragraph('附：营业执照扫描件')
        append_section_materials(doc,[PlaceholderItem(key='license',title='营业执照',attachToSection='资格资料')],media,['license'])
        assert len(list(doc.element.body.iter(qn('w:drawing'))))==1


def test_ocr_code_conflict_is_not_overridden_by_name():
    d=NS(summary=dump_facts({'role':'company','fields':[{'name':'名称','value':'青岑测试有限公司'},
        {'name':'统一社会信用代码','value':'91310000123456789B'}]}),tags=['bidder:青岑测试有限公司'])
    assert not document_matches_company(d,BidBrief(bidderName='青岑测试有限公司',bidderCreditCode='91310000123456789A'),key='credit')
    d.tags.append('credit_code:91310000123456789A')
    assert not document_matches_company(d,BidBrief(bidderName='青岑测试有限公司'),key='credit')


def test_readable_storage_path_is_used_when_file_key_is_stale(tmp_path,monkeypatch):
    valid=tmp_path/'proof.png';Image.new('RGB',(10,10),'blue').save(valid)
    monkeypatch.setattr(kb,'resolve_storage_path',lambda key:valid if key=='original' else tmp_path/'missing')
    assert kb._doc_file_path(NS(file_key='stale',storage_path='original'))==valid


@pytest.mark.asyncio
async def test_duplicate_parent_keys_preserve_all_candidates_for_company_scope(tmp_path,monkeypatch):
    paths=[tmp_path/('file'+str(i)+'.png') for i in range(2)]
    for p in paths:Image.new('RGB',(10,10),'blue').save(p)
    parents=[NS(public_id='p'+str(i),tags=['slot:license'],parent_id='',name='营业执照') for i in range(2)]
    monkeypatch.setattr(kb,'get_base_by_public_id',AsyncMock(return_value=NS(id=1)))
    monkeypatch.setattr(kb,'_list_docs',AsyncMock(return_value=parents))
    monkeypatch.setattr(kb,'_is_material_parent',lambda _:True)
    monkeypatch.setattr(kb,'_files_of',lambda d,_:[paths[int(d.public_id[-1])]])
    monkeypatch.setattr(kb,'list_slot_files',lambda _:[])
    # Distinct originals must survive until ownership filtering.
    Image.new('RGB',(10,10),'red').save(paths[1])
    result=await kb.resolve_attachments(None,[PlaceholderItem(key='license',title='营业执照')])
    assert set(result['license'])==set(paths)
