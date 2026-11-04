from copy import deepcopy
from pathlib import Path
import json
import zipfile
from datetime import date
from types import SimpleNamespace
import pytest
from docx import Document
from PIL import Image
from .test_ssot import inputs
from .ssot import build_ssot
from .goods_adapter import generate_goods,response_status,load_rules,write_business
from .goods_ooxml import patch_docx


def sample(tmp_path):
    manifest,final=inputs(tmp_path,[('invitation',['项目名称：测试货物','协议库存公开招标'],[])])
    return build_ssot(manifest,tmp_path,final)

@pytest.mark.parametrize('req,value,state', [('>=10','12','响应'),('>=10','5','偏离'),('≤10','10','响应'),('同等条件','同等条件','响应')])
def test_actual_guarantees(req,value,state):
    assert response_status(req,dict(guarantee=value,actor='审查人',basis='试验报告'))[0]==state


def test_never_invent_guarantees():
    assert response_status('10',None)==('待确认',None,'未提供有依据的实际保证值')
    assert response_status('>=10',dict(guarantee='5',status='响应',actor='人',basis='报告'))[0]=='待确认'


def test_preserves_template_and_warnings(tmp_path):
    ssot=sample(tmp_path);before=deepcopy(ssot.model_dump())
    rules=load_rules();rules.update(platform_max_file_bytes=1,platform_max_package_bytes=1)
    output=tmp_path/'result';r=generate_goods(ssot,tmp_path,output,rules=rules)
    assert len(r['technical'])==len(ssot.materials)==1
    rows=r['technical'][0]['rows']
    assert len(rows)==len(ssot.materials[0].parameter_points)
    assert all(x['status']=='待确认' for x in rows)
    assert all(x['written'] for x in rows)
    target=Path(r['technical'][0]['file'])
    source=tmp_path/ssot.documents[0].storage_path
    with zipfile.ZipFile(source) as a,zipfile.ZipFile(target) as b:
        assert a.namelist()==b.namelist()
        assert all(a.read(n)==b.read(n) for n in a.namelist() if n!='word/document.xml')
    assert any(w['type']=='capacity_exceeded' for w in r['warnings'])
    assert set(ssot.pending)<={w['source'] for w in r['warnings'] if isinstance(w.get('source'),str)}
    assert r['warnings'][0]['level']=='critical'
    assert ssot.model_dump()==before


def test_explicit_response_and_package(tmp_path):
    ssot=sample(tmp_path);m=ssot.materials[0];point=m.parameter_points[0]
    offer={m.solidified_id:{point.document_id+':'+point.locator:dict(guarantee='YJV22',status='响应',actor='审查人',basis='确认型号')}}
    result=generate_goods(ssot,tmp_path,tmp_path/'result',offers=offer,packages=[dict(key='bid_1',lot_key=None,package_code='P1',material_ids=[m.solidified_id])])
    assert result['technical'][0]['rows'][0]['status']=='响应'
    assert (tmp_path/'result/bid_1/business/business.docx').exists()


def test_original_nonempty_cell_not_overwritten(tmp_path):
    p=tmp_path/'source.docx';d=Document();t=d.add_table(rows=2,cols=2);t.cell(1,1).text='原文';d.save(p)
    result=patch_docx(p,tmp_path/'filled.docx',[dict(table=1,row=2,column=2,text='替换')])
    assert result['rejected'] and not result['applied']
    assert Document(tmp_path/'filled.docx').tables[0].cell(1,1).text=='原文'


def test_business_reuses_real_images_and_expiry(tmp_path):
    from api.services.tenders.schema import PlaceholderItem
    image=tmp_path/'scan.png';Image.new('RGB',(200,100),'white').save(image)
    slot=PlaceholderItem(key='certificate',title='真实证明材料')
    doc=SimpleNamespace(public_id='d',name='真实证明',expire_at=date(2000,1,1),is_perpetual=False)
    target=tmp_path/'business.docx'
    result=write_business(target,dict(slots=[slot],media={slot.key:[image]},documents=[(doc,image)]),load_rules())
    assert result['outcomes'][0]['kind']=='images'
    assert any(w['type']=='material_expiry' for w in result['warnings'])
    d=Document(target)
    assert len(d.inline_shapes)>0
    text='\n'.join(p.text for p in d.paragraphs)
    assert '2000-01-01' not in text and '已过期' not in text
    assert not any(p._p.xpath('.//w:pBdr') for p in d.paragraphs)


def test_missing_business_label_and_no_false_statement(tmp_path):
    target=tmp_path/'business.docx';result=write_business(target,dict(slots=[],media={},documents=[]),load_rules(),reviewed_texts=[dict(text='不应写入的承诺')])
    text='\n'.join(p.text for p in Document(target).paragraphs)
    assert '此处粘贴' in text and '不应写入的承诺' not in text
    assert any(w['type']=='business_text_pending' for w in result['warnings'])


def test_safe_output_scope_and_no_silent_omission(tmp_path):
    ssot=sample(tmp_path)
    with pytest.raises(ValueError):generate_goods(ssot,tmp_path,tmp_path/'unsafe',packages=[dict(key='../outside',material_ids=[])])
    assert not (tmp_path/'unsafe').exists()
    with pytest.raises(ValueError):generate_goods(ssot,tmp_path,tmp_path/'missing',packages=[dict(key='p',material_ids=[])])


def test_price_table_fragment_keeps_cells(tmp_path):
    manifest,final=inputs(tmp_path,[('invitation',['协议库存'],[]),('quotation',['原报价表'],[[['数量','单价','合价','备注'],['','','','']]])])
    ssot=build_ssot(manifest,tmp_path,final)
    r=generate_goods(ssot,tmp_path,tmp_path/'result')
    assert len(r['price'])==1
    source=Document(tmp_path/manifest['files'][-1]['storage_path'])
    target=Document(r['price'][0]['file'])
    assert source.tables[0]._tbl.xml==target.tables[0]._tbl.xml
    assert len(target.tables[0].rows)==2 and len(target.tables[0].columns)==4


def test_ambiguous_existing_files_do_not_become_missing_boxes(tmp_path):
    from api.services.tenders.schema import PlaceholderItem
    slot=PlaceholderItem(key='ambiguous',title='需选定的资料')
    output=tmp_path/'review.docx'
    result=write_business(output,dict(slots=[slot],media={},documents=[],unresolved_keys=[slot.key]),load_rules())
    doc=Document(output)
    assert not any(p._p.xpath('.//w:pBdr') for p in doc.paragraphs)
    assert result['outcomes'][0]['kind']=='selection_pending'
    assert any(w['type']=='business_unresolved' for w in result['warnings'])


def test_failed_file_is_not_expiry_checked(tmp_path,monkeypatch):
    from api.services.tenders import placeholders
    from api.services.tenders.schema import PlaceholderItem
    first=tmp_path/'ok.png';second=tmp_path/'bad.png'
    Image.new('RGB',(20,20)).save(first);second.write_bytes(b'broken')
    slot=PlaceholderItem(key='certificate',title='证明')
    d1=SimpleNamespace(public_id='ok',name='有效文件',expire_at=date(2100,1,1),is_perpetual=False)
    d2=SimpleNamespace(public_id='bad',name='未贴入的文件',expire_at=date(2000,1,1),is_perpetual=False)
    result=write_business(tmp_path/'business.docx',dict(slots=[slot],media={slot.key:[first,second]},documents=[(d1,first),(d2,second)]),load_rules())
    assert not any(w['type']=='material_expiry' for w in result['warnings'])
    assert any(w['type']=='business_render_incomplete' for w in result['warnings'])


def test_business_original_form_adapter(tmp_path):
    manifest,final=inputs(tmp_path,[('invitation',['协议库存'],[]),('notice',['商务原表'],[[['声明项目','确认内容'],['授权事项','']]])])
    ssot=build_ssot(manifest,tmp_path,final)
    doc=next(d for d in ssot.documents if d.role=='notice')
    result=generate_goods(ssot,tmp_path,tmp_path/'result',business_forms=[dict(package_key='scope_pending',document_id=doc.document_id,table_fragment=1,actor='确认人',basis='已审核声明',changes=[dict(table=1,row=2,column=2,text='经人工核对的应答')])])
    form=result['business'][0]['forms'][0]
    assert form['proof']['structure_verified']
    assert Document(form['file']).tables[0].cell(1,1).text=='经人工核对的应答'
    assert not any(w['type']=='business_forms_pending' for w in result['warnings'])
