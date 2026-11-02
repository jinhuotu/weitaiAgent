import hashlib

import pytest
from docx import Document
from docx.oxml.ns import qn
from PIL import Image

from api.services.tenders.section_materials import append_section_materials
from api.services.tenders.schema import PlaceholderItem


def outcomes(doc):
    return [m.get(qn('w:name')) for m in doc.element.body.iter(qn('w:bookmarkStart')) if m.get(qn('w:name'),'').startswith('result_')]


@pytest.mark.parametrize('company',['甲主体有限公司','乙主体有限公司','陌生丙主体有限公司'])
@pytest.mark.parametrize('has_file',[False,True])
def test_exact_identity_site_aliases_and_original_blocks(tmp_path,company,has_file):
    doc=Document();doc.add_paragraph('投标人：'+company)
    doc.add_paragraph('二、法定代表人身份证明',style='Heading 1')
    site=doc.add_paragraph('附：法定代表人身份证复印件或扫描件')
    following=doc.add_paragraph('原版后续说明及签字位置：________')
    doc.add_paragraph('三、授权委托书',style='Heading 1')
    doc.add_paragraph('原版授权条文，签字：________')
    originals=[(b,b.xml) for b in doc.element.body]
    file=tmp_path/'id.png';Image.new('RGB',(856,540),'blue').save(file)
    slots=[PlaceholderItem(key='id_legal',title='法定代表人身份证',attachToSection='法定代表人身份证明'),
        PlaceholderItem(key='material_other',title='法人代表的身份证复印件',attachToSection='法定代表人身份证明'),
        PlaceholderItem(key='conditional_form',title='授权委托书（如非法人亲自投标）',attachToSection='法定代表人身份证明')]
    media={'material_other':[file]} if has_file else {}
    append_section_materials(doc,slots,media,[s.key for s in slots],auth_enabled=False)
    assert outcomes(doc)==['result_'+hashlib.sha256(b'id_legal').hexdigest()[:20]+('_img' if has_file else '_box')]
    p=site._p.getnext();seen=False
    while p is not following._p:
        seen |= bool(list(p.iter(qn('w:drawing')))) if has_file else p.find('w:pPr/w:pBdr',p.nsmap) is not None
        p=p.getnext()
    assert seen
    assert all(b.xml==xml for b,xml in originals)
    once=doc.element.xml
    append_section_materials(doc,slots,media,[s.key for s in slots],auth_enabled=False)
    assert doc.element.xml==once


@pytest.mark.parametrize('enabled',[False,True])
@pytest.mark.parametrize('has_file',[False,True])
def test_explicit_agent_attachment_has_only_identity_result(tmp_path,enabled,has_file):
    doc=Document();doc.add_paragraph('授权委托书',style='Heading 1')
    site=doc.add_paragraph('附：委托代理人的身份证复印件或扫描件')
    stop=doc.add_paragraph('落款及后续原文')
    scan=tmp_path/'agent.png';Image.new('RGB',(856,540),'red').save(scan)
    slots=[PlaceholderItem(key='auth_document',title='授权委托书（如有）',attachToSection='授权委托书'),
        PlaceholderItem(key='id_agent',title='委托代理人身份证',attachToSection='授权委托书'),
        PlaceholderItem(key='other_agent',title='授权代理人的身份证扫描件',attachToSection='授权委托书')]
    append_section_materials(doc,slots,{'other_agent':[scan]} if has_file else {},[s.key for s in slots],auth_enabled=enabled)
    assert len(outcomes(doc))==1
    assert outcomes(doc)[0].endswith('_img' if has_file else '_box')
    assert site._p.getnext() is not stop._p
    assert not any('所需材料：授权委托书' in p.text for p in doc.paragraphs)


def test_existing_image_prevents_alias_box_and_new_files_upgrade_box(tmp_path):
    doc=Document();doc.add_paragraph('法定代表人身份证明',style='Heading 1')
    doc.add_paragraph('附：法定代表人身份证复印件')
    alias=PlaceholderItem(key='opaque',title='法人身份证',attachToSection='法定代表人身份证明')
    append_section_materials(doc,[alias],{},['opaque'])
    assert len(outcomes(doc))==1 and outcomes(doc)[0].endswith('_box')
    scan=tmp_path/'proof.png';Image.new('RGB',(80,50),'green').save(scan)
    standard=PlaceholderItem(key='id_legal',title='法定代表人身份证',attachToSection='法定代表人身份证明')
    append_section_materials(doc,[alias,standard],{'id_legal':[scan]},['opaque','id_legal'])
    assert len(outcomes(doc))==1 and outcomes(doc)[0].endswith('_img')
    append_section_materials(doc,[alias,standard],{},['opaque','id_legal'])
    assert len(outcomes(doc))==1 and outcomes(doc)[0].endswith('_img')
    assert not any(p._p.find('w:pPr/w:pBdr',p._p.nsmap) is not None for p in doc.paragraphs)


def test_distinct_people_are_not_merged(tmp_path):
    doc=Document();slots=[];media={}
    for index,name in enumerate(['张甲','李乙']):
        heading='授权委托书（'+name+'）'
        doc.add_paragraph(heading,style='Heading 1')
        doc.add_paragraph('附：委托代理人'+name+'身份证扫描件')
        slot=PlaceholderItem(key='person'+str(index),title='委托代理人'+name+'身份证',attachToSection=heading)
        path=tmp_path/(str(index)+'.png');Image.new('RGB',(80,50),['red','blue'][index]).save(path)
        slots.append(slot);media[slot.key]=[path]
    append_section_materials(doc,slots,media,[s.key for s in slots],auth_enabled=True)
    assert len(outcomes(doc))==2 and all(n.endswith('_img') for n in outcomes(doc))


def test_optional_same_material_and_table_boundary_are_not_duplicated():
    doc=Document();doc.add_paragraph('原版核验表',style='Heading 1')
    cue=doc.add_paragraph('附：学历证（如有）复印件')
    table=doc.add_table(rows=1,cols=1);table.cell(0,0).text='完整原版表格'
    stop=doc.add_paragraph('下一章',style='Heading 1')
    originals=[(b,b.xml) for b in doc.element.body]
    slots=[PlaceholderItem(key='education',title='学历证',attachToSection='原版核验表'),
           PlaceholderItem(key='variant',title='学历证（如有）复印件',attachToSection='原版核验表')]
    append_section_materials(doc,slots,{},['education','variant'])
    assert len(outcomes(doc))==1
    assert table._tbl.getnext() is not stop._p
    assert all(b.xml==xml for b,xml in originals)
    assert not list(cue._p.iter(qn('w:pBdr')))


def test_explicit_different_entities_keep_separate_materials(tmp_path):
    doc=Document();slots=[];media={}
    for index,company in enumerate(['甲主体有限公司','乙主体有限公司']):
        heading=company+'法定代表人身份证明'
        doc.add_paragraph(heading,style='Heading 1')
        doc.add_paragraph('附：'+company+'法人身份证扫描件')
        slot=PlaceholderItem(key='company'+str(index),title=company+'法人身份证',attachToSection=heading)
        path=tmp_path/(str(index)+'.png');Image.new('RGB',(80,50),['red','blue'][index]).save(path)
        slots.append(slot);media[slot.key]=[path]
    append_section_materials(doc,slots,media,[s.key for s in slots])
    assert len(outcomes(doc))==2 and all(name.endswith('_img') for name in outcomes(doc))
