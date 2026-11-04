from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import zipfile
import pytest
from docx import Document
from docx.shared import Pt
from lxml import etree
from .goods_layout import repair_technical_layout,repair_goods_package,W
from .goods_audit import build_review_catalog,confirm_pending_rows
from .goods_inputs import create_input_session,confirm_offer,compile_inputs
from .test_goods_adapter import sample


def test_layout_changes_only_authorized_properties(tmp_path):
    doc=Document();doc.add_heading('原标题',1);table=doc.add_table(rows=3,cols=2)
    table.cell(0,0).text='原表头';table.cell(1,0).text='原参数';run=table.cell(1,1).paragraphs[0].add_run('有来源的较长保证值')
    run.font.size=Pt(12);source=tmp_path/'source.docx';target=tmp_path/'fixed.docx';doc.save(source)
    before=sha256(source.read_bytes()).hexdigest()
    proof=repair_technical_layout(source,target,[dict(table=1,row=2,column=2)],font_scale=.85,min_readable_pt=9)
    assert proof['text_structure_verified'] and proof['fonts'][0]['selected_pt']==10.5
    assert .75<=proof['fonts'][0]['ratio']<=1
    with zipfile.ZipFile(source) as a,zipfile.ZipFile(target) as b:
        assert a.namelist()==b.namelist()
        assert all(a.read(n)==b.read(n) for n in a.namelist() if n!='word/document.xml')
        old=etree.fromstring(a.read('word/document.xml'));new=etree.fromstring(b.read('word/document.xml'))
        assert list(old.itertext())==list(new.itertext())
        assert len(new.findall('.//'+W+'cantSplit'))==3
        assert etree.tostring(old.find('.//'+W+'tblGrid'))==etree.tostring(new.find('.//'+W+'tblGrid'))
    assert sha256(source.read_bytes()).hexdigest()==before


def test_layout_refuses_small_font_missing_basis_and_unknown_cell(tmp_path):
    d=Document();d.add_table(rows=1,cols=1).cell(0,0).text='保证值';source=tmp_path/'source.docx';d.save(source)
    cells=[dict(table=1,row=1,column=1)]
    with pytest.raises(ValueError,match='75%'):repair_technical_layout(source,tmp_path/'small.docx',cells,font_scale=.7)
    with pytest.raises(ValueError,match='字号'):repair_technical_layout(source,tmp_path/'unknown.docx',cells)
    with pytest.raises(ValueError,match='不存在'):repair_technical_layout(source,tmp_path/'bad.docx',[dict(table=3,row=1,column=1)])
    assert not (tmp_path/'small.docx').exists()


def test_layout_package_preserves_original_and_business_data(tmp_path):
    from .goods_adapter import generate_goods
    ssot=sample(tmp_path);source=tmp_path/'draft';before=generate_goods(ssot,tmp_path,source)
    original_hash=sha256((source/'result.json').read_bytes()).hexdigest()
    revised=repair_goods_package(source,tmp_path/'repaired',layout_options=dict(font_scale=.9,rendered_default_pt=11,default_font_basis='测试夹具明确默认字号'))
    assert before['technical'][0]['rows']==revised['technical'][0]['rows']
    assert revised['technical'][0]['layout_proof']['rows_cannot_split']
    assert sha256((source/'result.json').read_bytes()).hexdigest()==original_hash
    assert revised['visual_validation']['visual_status']=='not_accepted'
    with pytest.raises(ValueError,match='新的'):repair_goods_package(source,source,layout_options={})


def test_pending_group_counts_and_explicit_review_keeps_passed_rows(tmp_path):
    ssot=sample(tmp_path);session=create_input_session(ssot);initial=build_review_catalog(ssot,session)
    first=initial['pending'][0]
    session=confirm_offer(session,first['solidified_id'],first['target'],first['requirement'],actor='人',basis='已核对夹具来源')
    catalog=build_review_catalog(ssot,session)
    assert catalog['automatic_count']==1
    assert sum(g['count'] for g in catalog['groups'])==catalog['pending_count']
    row=catalog['pending'][0]
    decision=dict(solidified_id=row['solidified_id'],target=row['target'],guarantee='具名确认的实际值',status='响应')
    updated=confirm_pending_rows(ssot,session,[decision],actor='复核人',basis='逐项查看产品依据')
    after=build_review_catalog(ssot,updated)
    assert after['pending_count']==catalog['pending_count']-1
    assert updated['review_history'][0]['decisions'][0]['prior']==row
    assert updated['review_history'][0]['decisions'][0]['decision']['confirmed_at']
    assert updated['offer_history'][0]==session['offer_history'][0]
    with pytest.raises(ValueError,match='已自动通过'):confirm_pending_rows(ssot,session,[dict(solidified_id=first['solidified_id'],target=first['target'],status='偏离')],actor='人',basis='依据')


def test_review_no_default_status_no_missing_guarantee(tmp_path):
    ssot=sample(tmp_path);session=create_input_session(ssot);row=build_review_catalog(ssot,session)['pending'][0]
    key=dict(solidified_id=row['solidified_id'],target=row['target'])
    with pytest.raises(ValueError,match='明确选择'):confirm_pending_rows(ssot,session,[key],actor='人',basis='依据')
    with pytest.raises(ValueError,match='最终值'):confirm_pending_rows(ssot,session,[dict(**key,status='响应')],actor='人',basis='依据')
    assert not session['offer_history']


def test_price_review_rejects_unknown_or_occupied_target(tmp_path):
    from .goods_review import submit_review
    ssot=sample(tmp_path);session=create_input_session(ssot)
    request=dict(operation='price',arguments=dict(document_id='unknown',table=1,row=1,column=1))
    with pytest.raises(ValueError,match='空白价格'):submit_review(ssot,session,request,root=tmp_path)
    from .test_ssot import inputs
    from .ssot import build_ssot
    folder=tmp_path/'priced';folder.mkdir()
    manifest,final=inputs(folder,[('quotation',['报价原表'],[[['数量','单价','总价','备注'],['2','100','','']]])])
    actual=build_ssot(manifest,folder,final);fresh=create_input_session(actual)
    doc=next(d for d in actual.documents if d.role=='quotation')
    args=dict(document_id=doc.document_id,table=1,row=2,column=2,package_key='P1',amount='123',actor='报价人',basis='已核对真实报价',currency='CNY',unit='台')
    with pytest.raises(ValueError,match='空白价格'):submit_review(actual,fresh,dict(operation='price',arguments=args),root=folder)
    args['column']=3
    updated=submit_review(actual,fresh,dict(operation='price',arguments=args),root=folder)
    compiled=compile_inputs(updated,actual)
    assert len(updated['price_history'])==1 and not compiled['price_fills']
    assert any(w['type']=='price_scope_pending' for w in compiled['input_provenance']['warnings'])


def test_reviewed_conflict_retains_original_and_resolution(tmp_path,monkeypatch):
    import api.services.tender_package.goods_inputs as inputs
    ssot=sample(tmp_path);session=create_input_session(ssot);target=session['targets'][0];key=(target['solidified_id'],target['target'])
    session=confirm_offer(session,*key,'reviewed',actor='最终确认人',basis='书面裁决',status='响应')
    decision=deepcopy(session['offer_history'][-1]);session['review_history']=[dict(decisions=[dict(solidified_id=key[0],target=key[1],decision=decision)])]
    warning=dict(type='product_parameter_conflict',solidified_id=key[0],target=target['source'],candidates=[dict(value='older')])
    session['product_history']=[dict(document_id='supplier')]
    monkeypatch.setattr(inputs,'match_product_offers',lambda *_:dict(offers={},warnings=[deepcopy(warning)]))
    r=compile_inputs(session,ssot)['input_provenance']['product_matching']['warnings'][0]
    assert r['type']=='product_parameter_conflict_reviewed'
    assert r['original_warning']==warning and r['resolution']==decision
