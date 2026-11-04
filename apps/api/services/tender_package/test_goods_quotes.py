from copy import deepcopy
from pathlib import Path
import pytest
from openpyxl import Workbook

from .goods_inputs import create_input_session,compile_inputs,confirm_packages
from .goods_quotes import read_quote_source,approve_quote_source,confirm_source_price
from .goods_workflow import apply_request
from .test_goods_adapter import sample


def quote_file(tmp_path,code,value=12.5):
    # Disposable unit fixture, never a business quotation artifact.
    path=tmp_path/'supplier.xlsx';w=Workbook();s=w.active;s.title='报价'
    s.append(['金额单位：人民币元']);s.append([])
    s.append(['技术规范ID','单位','数量','未含税单价（元）','含税合价（元）','税率（%）','最高限价'])
    s.append([code,'台',2,value,28.25,13,999]);s.append([])
    w.save(path);w.close();return path


def test_quote_reads_exact_values_and_cells_not_ceiling(tmp_path):
    ssot=sample(tmp_path);code=ssot.materials[0].solidified_id;p=quote_file(tmp_path,code)
    raw=p.read_bytes();r=read_quote_source(p)
    assert len(r['quotes'])==3 and p.read_bytes()==raw
    q=r['quotes'][0]
    assert q['amount']=='12.5' and q['cell']=='D4' and q['sheet']=='报价'
    assert q['raw_cells'][0]['value']==code and q['currency']=='CNY' and q['unit']=='台'
    assert r['quotes'][-1]['kind']=='tax_rate' and r['quotes'][-1]['unit']=='%'


@pytest.mark.parametrize('value',['=1+2','NaN',-1])
def test_quote_formula_invalid_and_negative_stay_pending(tmp_path,value):
    ssot=sample(tmp_path);r=read_quote_source(quote_file(tmp_path,ssot.materials[0].solidified_id,value))
    assert r['quotes'][0]['amount'] is None
    assert r['warnings'][0]['type']=='quote_value_pending'


def test_quote_approval_does_not_confirm_scope_or_offers(tmp_path):
    ssot=sample(tmp_path);session=create_input_session(ssot);p=quote_file(tmp_path,ssot.materials[0].solidified_id)
    approved=apply_request(ssot,session,dict(operation='quote_source',arguments=dict(path=str(p),actor='批准人',basis='已批准实际报价')))
    result=compile_inputs(approved,ssot)
    assert not result['packages'] and not result['price_fills'] and not result['offers']
    assert not session.get('quote_source_history')
    event=approved['quote_source_history'][0]
    assert event['confirmed_at'] and event['actor']=='批准人'
    assert any(w['type']=='approved_quotes_unassigned' for w in result['input_provenance']['warnings'])


def test_approved_price_through_confirmation_with_source_and_held_scope(tmp_path):
    from .test_ssot import inputs
    from .ssot import build_ssot
    manifest,final=inputs(tmp_path,[('invitation',['协议库存'],[[['分标编号','单位','数量'],['L1','台','5']]])])
    ssot=build_ssot(manifest,tmp_path,final);code=ssot.materials[0].solidified_id;session=create_input_session(ssot)
    approved=approve_quote_source(session,ssot,quote_file(tmp_path,code),actor='批准人',basis='实际价格批准')
    record=approved['quote_source_history'][0];q=record['quotes'][0]
    updated=confirm_source_price(approved,ssot,source_sha256=record['sha256'],quote_key=q['key'],package_key='P1',document_id='doc',table=1,row=2,column=3,actor='核对人',basis='已核对目标列')
    event=updated['price_history'][-1]
    assert event['text']=='12.5' and event['approved_quote_evidence']['quote']['cell']=='D4'
    assert not compile_inputs(updated,ssot)['price_fills']
    packages=[dict(key='P1',lot_key=ssot.lots[0].key,package_code='确认的包号',material_ids=[m.solidified_id for m in ssot.materials])]
    bound=confirm_packages(updated,ssot,packages,actor='人',basis='确认实际标包')
    assert compile_inputs(bound,ssot)['price_fills'][0]['text']=='12.5'
    Path(record['path']).write_bytes(b'changed')
    assert not compile_inputs(bound,ssot)['price_fills']


def test_missing_quote_approval_identity_rejected(tmp_path):
    ssot=sample(tmp_path);session=create_input_session(ssot)
    with pytest.raises(ValueError,match='确认人'):approve_quote_source(session,ssot,'unused',actor='',basis='依据')


def test_quote_ui_target_type_and_material_cannot_be_mixed(tmp_path,monkeypatch):
    from . import goods_review
    ssot=sample(tmp_path);code=ssot.materials[0].solidified_id
    session=approve_quote_source(create_input_session(ssot),ssot,quote_file(tmp_path,code),actor='批准人',basis='实际报价')
    approved=session['quote_source_history'][0];q=approved['quotes'][0]
    target=dict(document_id='doc',table=1,row=2,column=3,title='税率',raw_cells=[''])
    monkeypatch.setattr(goods_review,'price_targets',lambda *_:[target])
    request=dict(operation='source_price',arguments=dict(source_sha256=approved['sha256'],quote_key=q['key'],package_key='P',document_id='doc',table=1,row=2,column=3,actor='人',basis='依据'))
    with pytest.raises(ValueError,match='报价类型'):goods_review.submit_review(ssot,session,request,root=tmp_path)
    target['title']='单价';target['raw_cells']=['7777-987654321-99999']
    with pytest.raises(ValueError,match='固化ID'):goods_review.submit_review(ssot,session,request,root=tmp_path)
