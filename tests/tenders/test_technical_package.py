import asyncio
from copy import deepcopy
import json
from pathlib import Path

import pytest
from docx import Document
from docx.oxml.ns import qn

from api.services.tenders.schema import BidBrief,OutlineItem,PlaceholderItem,MaterialDemand
from api.services.tenders.technical_package import draft_package,structure,scoped_inputs
from api.services.tenders.technical_render import render,sync_toc

SOURCE=Path(__file__).parent/'fixtures/technical/organization-original.docx'


class Model:
    def __init__(self,brief):self.brief=brief;self.calls=[]
    async def complete(self,messages,*,mode):
        assert mode=='pro';self.calls.append(messages)
        spec=json.loads(messages[-1]['content'].rsplit('\n',1)[-1])
        if 'sections' in spec:
            return json.dumps({'sections':[{'id':p['id'],'body':
                self.brief.bidContent+'。'+self.brief.bidderName+'由'+self.brief.legalPersonName+'组织。信用代码：'+self.brief.bidderCreditCode+'。'+
                '依据本次工作范围分解实施任务，先复核接口和现场边界，形成技术交底记录。各责任岗位按工序完成准备、实施、自检与移交；质量岗位记录检查结果，不合格项隔离整改，经复验确认后进入后续工序。实施前核实资源、场地和作业许可，进度偏差由计划岗位提出资源调整并闭环复核。'
                } for p in spec['sections']]},ensure_ascii=False)
        return json.dumps({'tables':[{'id':t['id'],'rows':[[self.brief.bidContent+'拟配置']+['实施前核验']*(len(t['columns'])-1)],'note':'拟投入计划，实施前确认'} for t in spec['tables']],
            'stages':[{'id':'a','name':self.brief.bidContent+'准备','startDay':1,'endDay':2,'dependsOn':[]},
                      {'id':'b','name':'实施与验收','startDay':3,'endDay':5,'dependsOn':['a']}],
            'zones':[{'name':name,'x':x,'y':y,'w':.3,'h':.3} for name,x,y in [('作业区',.05,.05),('临时设施',.6,.05),('通行区',.05,.6)]],
            'note':'比例示意，现场复核后实施'},ensure_ascii=False)


def brief():
    return BidBrief(projectName='独立样本项目',bidContent='本次输入的专属作业对象',bidderName='独立样本技术有限公司',
        legalPersonName='测试姓名',bidderCreditCode='91300000123456789A',deliveryDays=10,
        layoutMode='outline',outlineChapter='第六章 投标文件格式',
        outlineItems=[OutlineItem(id='root',title='施工组织设计',kind='tech_chapter')])


def test_original_structure_is_thirteen_points_four_tables_and_two_figures():
    s=structure(brief(),Document(SOURCE))
    assert len(s['sections'])==13 and len(s['tables'])==4
    assert {f['kind'] for f in s['figures']}=={'schedule','layout'}


@pytest.mark.parametrize('anonymous',[False,True])
def test_original_template_bodies_tables_diagrams_and_identity(monkeypatch,anonymous):
    monkeypatch.setattr('api.services.tenders.pdf_format._word_format_file',lambda _:SOURCE)
    b=brief();b.techAnonymous=anonymous;b.invitationId='123456789abc'
    original=b.model_dump();client=Model(b)
    b.technicalPackage,notes=asyncio.run(draft_package(None,b,client=client))
    assert b.outlineItems[0].draftBody=='' and original['outlineItems']==b.model_dump()['outlineItems']
    doc=Document(SOURCE)
    from io import BytesIO
    from PIL import Image
    ghost=BytesIO();Image.new('RGB',(20,20),'red').save(ghost,format='PNG');ghost.seek(0)
    doc.add_picture(ghost)
    orphan=doc.paragraphs[-1]._p;orphan.getparent().remove(orphan)
    source_headings=[(p,p.text,deepcopy(p._p.pPr).xml if p._p.pPr is not None else None) for p in doc.paragraphs]
    headers=[deepcopy(t.rows[0]._tr).xml for t in doc.tables]
    render(doc,b)
    assert all(p.text==value and (deepcopy(p._p.pPr).xml if p._p.pPr is not None else None)==props for p,value,props in source_headings)
    assert [deepcopy(t.rows[0]._tr).xml for t in doc.tables]==headers
    assert all(any(c.text.strip() for row in t.rows[1:] for c in row.cells) for t in doc.tables)
    assert len(doc.inline_shapes)==2
    assert len([p for p in doc.part.package.parts if p.content_type.startswith('image/')])==2
    for p in b.technicalPackage['sections']:
        head=next(p0 for p0 in doc.paragraphs if p0.text==p['title'])
        assert head._p.getnext().tag==qn('w:p') and len(''.join(head._p.getnext().itertext()))>100
    blob=''.join(t.text or '' for t in doc.element.iter(qn('w:t')))
    assert (b.bidderName in blob)==(not anonymous)
    assert (b.legalPersonName in blob)==(not anonymous)
    assert (b.bidderCreditCode in blob)==(not anonymous)
    assert not list(doc.element.body.iter(qn('w:pBdr')))
    assert not [br for br in doc.element.body.iter(qn('w:br')) if br.get(qn('w:type'))=='page']
    once=doc.element.xml;render(doc,b);assert doc.element.xml==once


@pytest.mark.parametrize('scope,file',[('桥面修复','organization-original'),('实验仪器交付','goods-delivery'),('云平台迁移','service-migration'),('诊疗设备维保','medical-maintenance'),('陌生场景远程测绘','unseen-mapping')])
def test_input_scope_changes_generated_content_without_fixed_industry(monkeypatch,scope,file):
    source=SOURCE.parent/(file+'.docx')
    monkeypatch.setattr('api.services.tenders.pdf_format._word_format_file',lambda _:source)
    b=brief();b.bidContent=scope;b.bidderName=scope+'样本有限公司'
    package,_=asyncio.run(draft_package(None,b,client=Model(b)))
    assert all(scope in p['body'] for p in package['sections'])
    assert all(scope in t['rows'][0][0] for t in package['tables'])
    assert scope in package['stages'][0]['name']
    assert '充电桩' not in json.dumps(package,ensure_ascii=False)
    b.technicalPackage=package
    d=Document(source);render(d,b)
    assert len(d.inline_shapes)==2 and all(any(c.text.strip() for row in t.rows[1:] for c in row.cells) for t in d.tables)


def test_technical_boundary_rejects_business_and_plan_slots_but_keeps_proof(monkeypatch):
    monkeypatch.setattr('api.services.tenders.pdf_format._word_format_file',lambda _:SOURCE)
    b=brief();b.techAnonymous=False
    slots=[PlaceholderItem(key='license',title='营业执照'),PlaceholderItem(key='product',title='产品检测报告'),
           PlaceholderItem(key='plan',title='风险管理措施；')]
    b.materialDemands=[MaterialDemand(id='a',title='营业执照',text='提供营业执照',section='资格资料',slotKey='license',volume='full')]
    scoped,kept,media=scoped_inputs(b,slots,{'license':['bad'],'product':['good']})
    assert [s.key for s in kept]==['product'] and media=={'product':['good']}
    assert not scoped.materialDemands and b.materialDemands


def test_model_failure_never_becomes_forty_proof_boxes(monkeypatch):
    from common.errors import AppError
    monkeypatch.setattr('api.services.tenders.pdf_format._word_format_file',lambda _:SOURCE)
    class Empty:
        async def complete(self,*a,**k):return '{}'
    with pytest.raises(AppError,match='未生成实质正文'):
        asyncio.run(draft_package(None,brief(),client=Empty()))


def test_business_output_is_identical_with_or_without_technical_package(tmp_path,monkeypatch):
    from api.services.tenders.assemble import assemble_bid_docx
    from zipfile import ZipFile
    b=brief();b.layoutMode='outline';b.outlineChapter='';b.invitationId=''
    b.outlineItems=[OutlineItem(id='biz',title='现有商务说明',kind='unknown',body='保留本次商务响应原文。')];b.includeCommitment=False
    monkeypatch.setattr('api.services.tenders.technical_package.scoped_inputs',lambda *a:pytest.fail('Business must not enter technical scoping'))
    outputs=[]
    for package in [{},{'sections':[{'title':'仅属于技术卷','body':'不应进入商务卷'}]}]:
        current=b.model_copy(deep=True);current.technicalPackage=package
        path=tmp_path/f'{len(outputs)}.docx'
        assemble_bid_docx(current,path,volume='business',catalog_slots=[],catalog_media={})
        with ZipFile(path) as z:outputs.append({n:z.read(n) for n in z.namelist() if n.startswith('word/')})
    assert outputs[0]==outputs[1]


@pytest.mark.parametrize('field',['bidContent','techAnonymous'])
def test_cached_package_is_invalidated_when_project_input_changes(monkeypatch,field):
    monkeypatch.setattr('api.services.tenders.pdf_format._word_format_file',lambda _:SOURCE)
    b=brief();b.technicalPackage,_=asyncio.run(draft_package(None,b,client=Model(b)))
    previous=b.technicalPackage['sourceHash']
    if field=='bidContent':b.bidContent='完全不同的作业对象'
    else:b.techAnonymous=True
    m=Model(b);new,_=asyncio.run(draft_package(None,b,client=m))
    assert new['sourceHash']!=previous and m.calls
    assert all(b.bidContent in p['body'] for p in new['sections'])


def test_technical_coverage_does_not_report_business_materials(monkeypatch):
    from api.services.tenders.material_intents import coverage
    monkeypatch.setattr('api.services.tenders.pdf_format._word_format_file',lambda _:SOURCE)
    b=brief();b.technicalPackage={'sections':[]}
    b.materialDemands=[MaterialDemand(id='biz',title='营业执照',text='提供执照',slotKey='license',volume='full')]
    assert coverage(b,'technical')==[]
    assert len(coverage(b,'business'))==1 and len(b.materialDemands)==1


def test_named_mode_rejects_an_explicit_foreign_bidder(monkeypatch):
    from common.errors import AppError
    monkeypatch.setattr('api.services.tenders.pdf_format._word_format_file',lambda _:SOURCE)
    b=brief();b.techAnonymous=False
    b.technicalPackage,_=asyncio.run(draft_package(None,b,client=Model(b)))
    b.technicalPackage['sections'][0]['body']='投标人：其他主体有限公司。'+b.technicalPackage['sections'][0]['body']
    with pytest.raises(AppError,match='其他投标人'):
        render(Document(SOURCE),b)


def test_invalid_dependencies_are_repaired_without_redrafting_bodies(monkeypatch):
    monkeypatch.setattr('api.services.tenders.pdf_format._word_format_file',lambda _:SOURCE)
    b=brief()
    class Repair(Model):
        count=0
        async def complete(self,messages,*,mode):
            result=json.loads(await super().complete(messages,mode=mode))
            if 'stages' in result:
                self.count+=1
                if self.count==1:result['stages'][1]['startDay']=1
                else:assert '校验错误' in messages[-1]['content']
            return json.dumps(result,ensure_ascii=False)
    model=Repair(b);package,_=asyncio.run(draft_package(None,b,client=model))
    assert model.count==2 and package['stages'][1]['startDay']==3
    assert len([m for m in model.calls if 'anonymous' in json.loads(m[-1]['content'].rsplit('\n',1)[-1])])==7


def test_incomplete_manual_point_is_not_silently_overwritten(monkeypatch):
    from common.errors import AppError
    monkeypatch.setattr('api.services.tenders.pdf_format._word_format_file',lambda _:SOURCE)
    b=brief();title=structure(b)['sections'][0]['title']
    b.outlineItems.append(OutlineItem(id='manual',title=title,kind='tech_chapter',draftBody='人工待完善正文',draftStatus='edited'))
    with pytest.raises(AppError,match='未自动覆盖'):
        asyncio.run(draft_package(None,b,client=Model(b)))


def test_existing_technical_inputs_and_kb_style_remain_available(monkeypatch):
    monkeypatch.setattr('api.services.tenders.pdf_format._word_format_file',lambda _:SOURCE)
    async def recall(*args,**kwargs):
        assert kwargs['quiet'] is True
        return [{'content':'历史主体有限公司；250万元；工序、质量检查与验收记录。'}],[]
    monkeypatch.setattr('api.services.tenders.extract._recall_knowledge',recall)
    b=brief();b.techPlanKbIds=['style'];b.constructionPlan='本次用户输入的专属技术安排'
    model=Model(b);asyncio.run(draft_package(object(),b,client=model))
    messages=''.join(m[-1]['content'] for m in model.calls)
    assert b.constructionPlan in messages and 'styleReference' in messages
    assert '历史主体有限公司' not in messages and '250万元' not in messages


def test_schedule_dates_only_come_from_the_invitation_and_conflicts_are_explicit(monkeypatch):
    from api.services.tenders.technical_render import _schedule_date_note
    b=brief();b.bidDate='2040-01-01';b.deliveryDays=5
    monkeypatch.setattr('api.services.tenders.assets.load_invitation_text',lambda _:'计划开工日期：2028-05-01；计划竣工日期：2028-05-07')
    note=_schedule_date_note(b)
    assert '2028-05-01' in note and '2028-05-07' in note and '口径差异' in note and b.bidDate not in note
