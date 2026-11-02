"""Unseen material names, all-unit semantic review and physical outcome regression."""
import json
import re
from pathlib import Path

import pytest
from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from PIL import Image

from api.services.tenders.material_intents import (
    applicable, coverage, enforce_material_outcomes, material_key,
    recognize_material_demands, source_units, structural_demands,
)
from api.services.tenders.placeholders import collect_slots, this_bid_keys
from api.services.tenders.schema import BidBrief, MaterialDemand, OutlineItem, PlaceholderItem


class SemanticClient:
    def __init__(self, *, incomplete=False):
        self.seen = []
        self.incomplete = incomplete

    async def complete(self, messages, **_):
        data = json.loads(messages[-1]['content'])
        self.seen.extend(data['units'])
        return json.dumps({'reviewedUnitIds':[] if self.incomplete else [u['id'] for u in data['units']],
            'requirements':[{'sourceId':u['id'],'evidence':u['text'],'title':title,'key':''}
                for u in data['units'] for title in re.findall('《([^》]+)》',u['text'])]},ensure_ascii=False)


def brief():
    return BidBrief(bidderName='回归资料主体有限公司',projectName='本次采购项目',
        outlineItems=[OutlineItem(id='one',title='一、资格材料',kind='company'),
                      OutlineItem(id='two',title='（一）专项佐件',kind='company',level=2)])


@pytest.mark.asyncio
async def test_semantics_reviews_every_unit_even_when_no_lexical_material_trigger():
    b=brief();client=SemanticClient()
    text='一、资格材料\n响应包内不可缺少《弧衡矩阵》及《霁序谱录》。\n普通评审说明。'
    assert not structural_demands(b,source_units(b,text),[])
    await recognize_material_demands(b,text=text,catalog=[],client=client)
    assert {'弧衡矩阵','霁序谱录'} == {d.title for d in b.materialDemands}
    assert b.materialIntentComplete and not b.materialIntentNotes
    assert any('普通评审说明' in u['text'] for u in client.seen)
    assert all(d.text in text and d.section=='一、资格材料' for d in b.materialDemands)
    assert all(d.slotKey.startswith('material_') for d in b.materialDemands)
    calls=len(client.seen)
    await recognize_material_demands(b,text=text,catalog=[],client=client)
    assert len(client.seen)==calls


@pytest.mark.asyncio
async def test_incomplete_semantic_review_is_explicit_and_has_a_review_placeholder():
    b=brief()
    await recognize_material_demands(b,text='一、资格材料\n响应包不可缺少《陌生佐件》。',client=SemanticClient(incomplete=True))
    assert not b.materialIntentComplete and b.materialIntentNotes
    assert any(d.title=='陌生佐件' for d in b.materialDemands)
    assert any(d.reviewRequired for d in b.materialDemands)
    doc=Document();doc.add_paragraph('一、资格材料',style='Heading 1')
    enforce_material_outcomes(doc,b,volume='full')
    assert all(row['status']=='placeholder' for row in coverage(b,'full'))
    pending_keys={d.slotKey for d in b.materialDemands if d.reviewRequired}
    await recognize_material_demands(b,text='一、资格材料\n响应包不可缺少《陌生佐件》。',client=SemanticClient())
    assert b.materialIntentComplete and not any(d.reviewRequired for d in b.materialDemands)
    assert not pending_keys & set(b.requiredSlotKeys)
    assert not pending_keys & {s.key for s in b.extraPlaceholders}


def test_unknown_upload_safe_keys_and_routing_survive_catalog_filters():
    assert material_key('《从未出现过的佐件》')==material_key('从未出现过的佐件')
    key=material_key('从未出现过的佐件')
    extra=[PlaceholderItem(key=key,title='从未出现过的佐件',hint='不可缺少的响应附件',attachToSection='（一）专项佐件')]
    required,include=this_bid_keys(extra=extra,catalog=[],required_keys=[key],include_keys=[key])
    assert key in required and key in include
    slots=collect_slots(extra,catalog=[],include_keys=include)
    found=next(s for s in slots if s.key==key)
    assert found.title==extra[0].title and found.attachToSection=='（一）专项佐件'


def test_nested_numbered_paragraphs_and_table_cells_are_reviewed(tmp_path):
    doc=Document();doc.add_paragraph('一、资格材料',style='Heading 1')
    doc.add_paragraph('（一）专项佐件',style='Heading 2')
    p=doc.add_paragraph('响应包内不可缺少《异名附件》。',style='Heading 9')
    num=OxmlElement('w:numPr');level=OxmlElement('w:ilvl');level.set(qn('w:val'),'2');num.append(level)
    p._p.get_or_add_pPr().append(num)
    doc.add_table(rows=1,cols=1).cell(0,0).text='另随《表格内佐件》。'
    path=tmp_path/'source.docx';doc.save(path)
    units=source_units(brief(),source_path=path)
    row=next(u for u in units if '异名附件' in u.text)
    assert row.level==3
    assert any('表格内佐件' in u.text for u in units)


@pytest.mark.asyncio
async def test_extractor_text_is_not_lost_when_word_body_is_readable(tmp_path):
    doc=Document();doc.add_paragraph('一、资格材料',style='Heading 1')
    doc.add_paragraph('正文内容。')
    path=tmp_path/'source.docx';doc.save(path)
    text='一、资格材料\n正文内容。\n响应包不可缺少《文本框恢复佐件》。'
    b=brief();client=SemanticClient()
    await recognize_material_demands(b,text=text,source_path=path,client=client)
    row=next(d for d in b.materialDemands if d.title=='文本框恢复佐件')
    assert row.section=='一、资格材料' and b.materialIntentComplete
    assert sum(u['text']=='正文内容。' for u in client.seen)==1


@pytest.mark.asyncio
async def test_verified_empty_ledger_does_not_manufacture_default_materials():
    from api.services.tenders.material_intents import prepare_structural_demands
    class NoObligations:
        async def complete(self,messages,**_):
            units=json.loads(messages[-1]['content'])['units']
            return json.dumps({'reviewedUnitIds':[u['id'] for u in units],'requirements':[]})
    b=brief()
    await recognize_material_demands(b,text='本次仅需在线确认，不需提交附件。',client=NoObligations())
    prepare_structural_demands(b)
    required,include=this_bid_keys(material_demands=b.materialDemands,source_text='身份证 营业执照')
    assert b.materialIntentComplete and not b.materialDemands and not required and not include


@pytest.mark.asyncio
async def test_exempt_material_is_removed_but_required_exemption_statement_is_retained():
    class Exemptions:
        async def complete(self,messages,**_):
            units=json.loads(messages[-1]['content'])['units']
            return json.dumps({'reviewedUnitIds':[u['id'] for u in units],
                'requirements':[{'sourceId':u['id'],'evidence':u['text'],'title':title}
                    for u in units for title in re.findall('《([^》]+)》',u['text']) if '免交' not in u['text']],
                'exemptions':[{'sourceId':u['id'],'evidence':u['text'],'title':'履约资源札记'}
                    for u in units if '免交' in u['text']]},ensure_ascii=False)
    b=brief();text='一、资格材料\n格式中应附《履约资源札记》。\n本次免交《履约资源札记》。\n须提供《豁免依据声明》。'
    client=Exemptions()
    await recognize_material_demands(b,text=text,client=client)
    assert {d.title for d in b.materialDemands}=={'豁免依据声明'}
    assert material_key('履约资源札记') not in b.requiredSlotKeys and b.materialExemptions
    await recognize_material_demands(b,text=text,client=client)
    assert {d.title for d in b.materialDemands}=={'豁免依据声明'}


def demand(name,section='（一）专项佐件',key=None):
    return MaterialDemand(id='demand_'+material_key(name)[9:],text='响应包中须有《'+name+'》。',title=name,
                          slotKey=key or material_key(name),section=section,level=2)


def test_two_unmapped_materials_in_one_subsection_each_get_a_physical_box():
    b=brief();b.materialDemands=[demand('弧衡矩阵'),demand('霁序谱录')]
    doc=Document();doc.add_paragraph('一、资格材料',style='Heading 1')
    doc.add_paragraph('（一）专项佐件',style='Heading 2');doc.add_paragraph('原正文。')
    doc.add_paragraph('二、报价文件',style='Heading 1')
    enforce_material_outcomes(doc,b,volume='full')
    boxes=[p for p in doc.paragraphs if p._p.find('w:pPr/w:pBdr',p._p.nsmap) is not None]
    assert len(boxes)==2
    assert all(d.outcomes['full']=='placeholder' for d in b.materialDemands)
    end=next(i for i,p in enumerate(doc.paragraphs) if p.text=='二、报价文件')
    positions={p._p:i for i,p in enumerate(doc.paragraphs)}
    assert all(positions[p._p]<end for p in boxes)
    once=doc.element.xml
    enforce_material_outcomes(doc,b,volume='full')
    assert doc.element.xml==once


def test_no_matching_section_falls_back_after_body_not_under_toc():
    b=brief();b.materialDemands=[demand('未知附件',section='不存在的源章节')]
    doc=Document();doc.add_paragraph('一、资格材料',style='Heading 1');doc.add_paragraph('原正文末段。')
    enforce_material_outcomes(doc,b,volume='full')
    text='\n'.join(p.text for p in doc.paragraphs)
    assert text.index('原正文末段')<text.index('其他资料（资料需求补充）')<text.index('未知附件')


def test_same_file_across_unknown_keys_is_pasted_once(tmp_path):
    path=tmp_path/'proof.png';Image.new('RGB',(90,60),'red').save(path)
    duplicate=tmp_path/'copy.png';duplicate.write_bytes(path.read_bytes())
    b=brief();b.materialDemands=[demand('佐件甲'),demand('佐件乙')]
    doc=Document();doc.add_paragraph('一、资格材料',style='Heading 1');doc.add_paragraph('（一）专项佐件',style='Heading 2')
    media={b.materialDemands[0].slotKey:[path],b.materialDemands[1].slotKey:[duplicate]}
    enforce_material_outcomes(doc,b,media=media,volume='full')
    assert len(list(doc.element.iter(qn('w:drawing'))))==1
    assert all(d.outcomes['full'] in {'filled','pasted'} for d in b.materialDemands)


def test_partly_shared_files_do_not_repeat_the_already_pasted_page(tmp_path):
    first=tmp_path/'first.png';second=tmp_path/'second.png'
    Image.new('RGB',(90,60),'red').save(first);Image.new('RGB',(90,60),'green').save(second)
    b=brief();b.materialDemands=[demand('甲佐件'),demand('乙佐件')]
    doc=Document();doc.add_paragraph('一、资格材料',style='Heading 1');doc.add_paragraph('（一）专项佐件',style='Heading 2')
    media={b.materialDemands[0].slotKey:[first],b.materialDemands[1].slotKey:[first,second]}
    enforce_material_outcomes(doc,b,media=media,volume='full')
    assert len(list(doc.element.iter(qn('w:drawing'))))==2
    for picture in list(doc.element.iter(qn('w:drawing'))):picture.getparent().remove(picture)
    enforce_material_outcomes(doc,b,media=media,volume='full')
    assert len(list(doc.element.iter(qn('w:drawing'))))==2
    names=[n.get(qn('w:name')) for n in doc.element.iter(qn('w:bookmarkStart'))]
    assert len(names)==len(set(names))


def test_filled_form_is_detected_and_stale_status_cannot_satisfy_a_new_blank_form():
    b=brief();d=demand('登记情况表');d.mode='form';b.materialDemands=[d]
    def document(value):
        doc=Document();doc.add_paragraph('（一）专项佐件',style='Heading 2')
        table=doc.add_table(rows=2,cols=2)
        table.cell(0,0).text='字段';table.cell(0,1).text='响应'
        table.cell(1,0).text='投标人';table.cell(1,1).text=value
        return doc
    filled=document(b.bidderName)
    enforce_material_outcomes(filled,b,volume='full')
    assert d.outcomes['full']=='filled'
    blank=document('')
    enforce_material_outcomes(blank,b,volume='full')
    assert d.outcomes['full']=='placeholder'
    assert any(p._p.find('w:pPr/w:pBdr',p._p.nsmap) is not None for p in blank.paragraphs)


@pytest.mark.asyncio
async def test_malformed_or_ungrounded_semantic_response_does_not_claim_completion():
    class Ungrounded:
        async def complete(self,messages,**_):
            units=json.loads(messages[-1]['content'])['units']
            return json.dumps({'reviewedUnitIds':[u['id'] for u in units],
                'requirements':[{'sourceId':units[-1]['id'],'evidence':'不在原文里的要求','title':'捏造附件'}]})
    b=brief()
    await recognize_material_demands(b,text='一、资格材料\n原文只有一段正常说明。',client=Ungrounded())
    assert not b.materialIntentComplete
    assert all(d.title!='捏造附件' for d in b.materialDemands)
    assert any(d.reviewRequired for d in b.materialDemands)


@pytest.mark.asyncio
async def test_semantic_chunks_cover_the_tail_of_a_previously_unseen_invitation():
    b=brief();client=SemanticClient()
    paragraphs=['普通说明'+str(i)+'：'+'按本次要求核对。'*45 for i in range(48)]
    text='一、资格材料\n'+'\n'.join(paragraphs)+'\n响应包不可缺少《末段异名附件》。'
    await recognize_material_demands(b,text=text,client=client)
    assert b.materialIntentComplete and any(d.title=='末段异名附件' for d in b.materialDemands)
    assert {u['id'] for u in client.seen}=={u.id for u in source_units(b,text)}


def test_subsection_without_builtin_heading_style_still_owns_its_placeholder():
    b=brief();b.materialDemands=[demand('异名附件',section='特殊子项')]
    doc=Document();doc.add_paragraph('一、资格材料',style='Heading 1')
    for label in ['特殊子项','下一个子项']:
        p=doc.add_paragraph(label)
        num=OxmlElement('w:numPr');lvl=OxmlElement('w:ilvl');lvl.set(qn('w:val'),'1');num.append(lvl)
        p._p.get_or_add_pPr().append(num)
        doc.add_paragraph(label+'正文。')
    enforce_material_outcomes(doc,b,volume='full')
    paragraphs=doc.paragraphs;box=next(i for i,p in enumerate(paragraphs) if '【待补充】' in p.text)
    assert next(i for i,p in enumerate(paragraphs) if p.text=='特殊子项')<box<next(i for i,p in enumerate(paragraphs) if p.text=='下一个子项')


def test_conditional_agent_material_is_removed_when_agent_is_not_used():
    from api.services.tenders.material_intents import apply_demand_slots
    b=brief();b.authEnabled=True;d=demand('代理证件',key='id_agent');d.condition='agent'
    apply_demand_slots(b,[d]);assert 'id_agent' in b.requiredSlotKeys
    b.authEnabled=False;apply_demand_slots(b,[d])
    assert 'id_agent' not in b.requiredSlotKeys and not applicable(d,b,'full')


@pytest.mark.asyncio
async def test_full_material_source_survives_old_qa_cache_limit(tmp_path,monkeypatch):
    from api.services.tenders import assets
    monkeypatch.setattr(assets,'tender_invitations_dir',lambda:tmp_path)
    text='一、资格材料\n'+('\n普通说明'+('检查说明。'*220))*80+'\n响应包不可缺少《全文末尾佐件》。'
    ident=assets.save_invitation_text(text,stem='abcdef123456')
    assert len(assets.load_invitation_text(ident))<len(text)
    b=brief();b.invitationId=ident;client=SemanticClient()
    await recognize_material_demands(b,text=text,client=client,persist_source=True)
    assert any(d.title=='全文末尾佐件' for d in b.materialDemands)
    count=len(client.seen)
    await recognize_material_demands(b,client=client)
    assert len(client.seen)==count and b.materialIntentComplete
    assert assets.load_material_source_text(ident)==text


@pytest.mark.parametrize('mode',['requirements','chapter5'])
def test_deep_copy_and_template_renderers_return_verified_coverage(tmp_path,monkeypatch,mode):
    from api.services.tenders.generate import generate_bid,generate_pair
    monkeypatch.setattr('api.services.tenders.generate.tenders_output_dir',lambda:tmp_path)
    b=brief();b.generateVolume='full';b.layoutMode=mode;b.requirementsDriven=mode=='requirements'
    b.includeCommitment=False;b.materialDemands=[demand('异名材料',section='一、资格材料')]
    slots=[PlaceholderItem(key=b.materialDemands[0].slotKey,title='异名材料',attachToSection='一、资格材料',demandIds=[b.materialDemands[0].id])]
    out=generate_bid(b,catalog_slots=slots,catalog_media={},catalog_facts=[])
    assert out['materialCoverage']['full'][0]['status']=='placeholder'
    assert b.materialDemands[0].outcomes['full']=='placeholder'
    if mode=='requirements':
        b.generateBoth=True
        pair=generate_pair(b,catalog_slots=slots,catalog_media={},catalog_facts=[])
        assert pair['materialCoverage']['business'][0]['status']=='placeholder'


def test_same_family_different_material_titles_have_unique_bookmarks():
    from api.services.tenders.material_intents import SourceUnit, _demand
    b=brief();unit=SourceUnit('u','须提供甲年度报告及乙年度报告。','（一）专项佐件',2)
    catalog=[PlaceholderItem(key='finance',title='财务审计报告')]
    rows=[_demand(b,unit,title,unit.text,catalog,suggested='finance') for title in ['甲年度报告','乙年度报告']]
    assert len({d.id for d in rows})==2
    b.materialDemands=rows
    doc=Document();doc.add_paragraph('（一）专项佐件',style='Heading 2')
    enforce_material_outcomes(doc,b,volume='full')
    names=[n.get(qn('w:name')) for n in doc.element.iter(qn('w:bookmarkStart'))]
    assert len(names)==len(set(names))


@pytest.mark.asyncio
@pytest.mark.parametrize('company,scope', [('初禾建设有限公司','工程'),('远岚设备有限公司','货物'),('澄序服务有限公司','服务')])
async def test_unseen_company_and_invitation_full_generation_preserves_original_toc(tmp_path,monkeypatch,company,scope):
    from api.services.tenders.generate import generate_bid
    from api.services.tenders.native_format import validate_native_toc
    from api.services.tenders.document import _pageref_bookmark
    source=Document();source.add_paragraph('第六章 投标文件格式');source.add_paragraph('投 标 文 件')
    source.add_paragraph('投标人：____');source.add_paragraph('目录')
    titles=['一、资格材料','（一）专项佐件','二、报价文件']
    for title in titles:source.add_paragraph(title+'\t1')
    source.add_page_break()
    for index,title in enumerate(titles):
        source.add_paragraph(title,style='Heading 2' if index==1 else 'Heading 1')
        if index==1:source.add_paragraph('响应包内不可缺少《'+scope+'专属异名佐件》。')
    path=tmp_path/'native.docx';source.save(path)
    path.with_name(path.name+'.source').write_text('docx-raw',encoding='utf-8')
    original=path.read_bytes()
    b=brief();b.bidderName=company;b.generateVolume='full';b.invitationId='abcdef123456'
    b.layoutMode='outline';b.outlineChapter='第六章 投标文件格式';b.formatConfirmed=True;b.includeCommitment=False
    b.outlineItems.append(OutlineItem(id='q',title='二、报价文件',kind='quote'))
    await recognize_material_demands(b,source_path=path,client=SemanticClient())
    monkeypatch.setattr('api.services.tenders.pdf_format._word_format_file',lambda _:path)
    monkeypatch.setattr('api.services.tenders.generate.tenders_output_dir',lambda:tmp_path)
    result=generate_bid(b,catalog_slots=collect_slots(b.extraPlaceholders,include_keys=b.includeSlotKeys),catalog_media={},catalog_facts=[])
    out=Document(tmp_path/result['docxFile']);validate_native_toc(out)
    rows=[p for p in out.paragraphs if _pageref_bookmark(p)]
    assert [p.text.split('\t')[0] for p in rows]==titles
    assert all(p.text.rsplit('\t',1)[-1].isdigit() for p in rows)
    assert all(r['status']=='placeholder' for r in result['materialCoverage']['full'])
    names=[e.get(qn('w:name')) for e in out.element.iter(qn('w:bookmarkStart'))]
    assert len(names)==len(set(names))
    assert path.read_bytes()==original
