from copy import deepcopy
from pathlib import Path
import pytest
from docx import Document
from PIL import Image
from .test_goods_adapter import sample
from .goods_inputs import create_input_session,confirm_offer,confirm_price,confirm_packages,compile_inputs,read_product_source,match_product_offers
from .goods_render import render_package,review_page,CommandRenderer,CHECKS


def test_manual_inputs_history(tmp_path):
    ssot=sample(tmp_path);session=create_input_session(ssot);target=session['targets'][0]
    a=confirm_offer(session,target['solidified_id'],target['target'],'真实值',actor='审查人',basis='产品说明')
    b=confirm_offer(a,target['solidified_id'],target['target'],'纠正后的值',actor='审查人',basis='复核')
    result=compile_inputs(b,ssot)
    assert len(b['offer_history'])==2 and not session['offer_history']
    assert result['offers'][target['solidified_id']][target['target']]['guarantee']=='纠正后的值'
    with pytest.raises(ValueError):confirm_offer(session,target['solidified_id'],target['target'],'',actor='人',basis='依据')


def product(tmp_path,name='supplier.docx',value='YJV22'):
    p=tmp_path/name;d=Document();t=d.add_table(rows=2,cols=3)
    for c,text in zip(t.rows[0].cells,['参数名称','实际值','单位']):c.text=text
    for c,text in zip(t.rows[1].cells,['产品型号规格',value,'无']):c.text=text
    d.save(p);return p


def test_product_matching_provenance(tmp_path):
    ssot=sample(tmp_path);code=ssot.materials[0].solidified_id
    p=product(tmp_path)
    card=read_product_source(p,'library-doc',solidified_ids=[code],binding_actor='审查人',binding_basis='确认本次选用产品')
    result=match_product_offers(ssot,[card])
    assert result['offers'][code]
    offer=next(iter(result['offers'][code].values()))
    assert offer['guarantee']=='YJV22' and offer['sources'][0]['locator']=='table:1/row:2'
    assert offer['sources'][0]['sha256']==card['sha256']
    card['parameters'][0]['value']='调用方伪造值'
    assert next(iter(match_product_offers(ssot,[card])['offers'][code].values()))['guarantee']=='YJV22'


def test_product_conflict_and_binding(tmp_path):
    ssot=sample(tmp_path);code=ssot.materials[0].solidified_id
    a=read_product_source(product(tmp_path), 'a',solidified_ids=[code],binding_actor='人',binding_basis='选型')
    b=read_product_source(product(tmp_path,'second.docx','不同型号'), 'b',solidified_ids=[code],binding_actor='人',binding_basis='选型')
    r=match_product_offers(ssot,[a,b])
    assert not r['offers'] and r['warnings'][0]['type']=='product_parameter_conflict'
    a['binding_actor']=None
    assert not match_product_offers(ssot,[a])['offers']


def test_price_waits_for_scope(tmp_path):
    ssot=sample(tmp_path);session=create_input_session(ssot)
    revised=confirm_price(session,package_key='P1',document_id='doc',table=1,row=2,column=3,amount='123.45',actor='报价人',basis='实际报价',currency='CNY',unit='台')
    compiled=compile_inputs(revised,ssot)
    assert not compiled['price_fills'] and compiled['input_provenance']['warnings']
    with pytest.raises(ValueError):confirm_price(session,package_key='P1',document_id='d',table=1,row=2,column=3,amount='NaN',actor='人',basis='报价',currency='CNY',unit='台')


def test_no_renderer_does_not_claim_visual_pass(tmp_path):
    d=Document();d.save(tmp_path/'test.docx')
    result=render_package(tmp_path,CommandRenderer(['nonexistent-renderer-command']))
    assert result['structure_only'] and result['visual_status']=='not_accepted'
    assert len(result['documents'])==1 and result['documents'][0]['status']=='not_rendered'


def test_render_requires_each_page_review(tmp_path):
    d=Document();d.save(tmp_path/'test.docx')
    class FakeRenderer:
        def available(self):return True
        def render(self,source,dest):
            dest.mkdir(parents=True);pages=[]
            for i in range(2):
                p=dest/f'{i}.png';Image.new('RGB',(10,10),'white').save(p);pages.append(p)
            return pages
    report=render_package(tmp_path,FakeRenderer())
    assert report['visual_status']=='not_accepted'
    paths=[p['path'] for p in report['documents'][0]['pages']]
    first=review_page(report,paths[0],actor='核对人',basis='逐页核对',checks=dict.fromkeys(CHECKS,True))
    assert first['visual_status']=='not_accepted'
    accepted=review_page(first,paths[1],actor='核对人',basis='逐页核对',checks=dict.fromkeys(CHECKS,True))
    assert accepted['visual_status']=='accepted'
    (tmp_path/'test.docx').write_bytes(b'changed')
    with pytest.raises(ValueError):review_page(accepted,paths[1],actor='人',basis='核对',checks=dict.fromkeys(CHECKS,True))


def test_failed_render_is_explicit(tmp_path):
    Document().save(tmp_path/'test.docx')
    class FailedRenderer:
        def available(self):return True
        def render(self,source,dest):raise RuntimeError('failure')
    report=render_package(tmp_path,FailedRenderer())
    assert report['documents'][0]['status']=='failed' and report['visual_status']=='not_accepted'


def test_confirmed_packages_and_actual_price_fill(tmp_path):
    from .test_ssot import inputs
    from .ssot import build_ssot
    from .goods_adapter import generate_goods
    manifest,final=inputs(tmp_path,[('invitation',['协议库存'],[[['分标编号','单位','数量'],['L1','台','5']]]),('quotation',['原报价表'],[[['数量','单价','合价','备注'],['','','','']]])])
    ssot=build_ssot(manifest,tmp_path,final);session=create_input_session(ssot)
    code=ssot.materials[0].solidified_id;lot=ssot.lots[0].key
    session=confirm_packages(session,ssot,[dict(key='P1',lot_key=lot,package_code='包号由人工确认',material_ids=[code])],actor='审查人',basis='确认投标范围')
    doc=next(d for d in ssot.documents if d.role=='quotation')
    session=confirm_price(session,package_key='P1',document_id=doc.document_id,table=1,row=2,column=2,amount='123.45',actor='报价人',basis='审批后的实际报价',currency='CNY',unit='台')
    r=generate_goods(ssot,tmp_path,tmp_path/'result',**compile_inputs(session,ssot))
    assert Document(r['price'][0]['file']).tables[0].cell(1,1).text=='123.45'
    assert r['price'][0]['reviewed_fills'][0]['basis']=='审批后的实际报价'


def test_real_library_selection_is_traced(tmp_path):
    from .goods_inputs import select_library_files
    from hashlib import sha256
    p=tmp_path/'scan.png';Image.new('RGB',(10,10)).save(p)
    snapshot=dict(candidate_media={'slot':[dict(document_id='real',path=str(p),sha256=sha256(p.read_bytes()).hexdigest())]},media={'slot':[]},unresolved_keys=['slot'])
    selected=select_library_files(snapshot,{'slot':['real']},actor='选择人',basis='本次选用资料')
    assert selected['media']['slot']==[str(p)] and not selected['unresolved_keys']
    assert selected['selection_history'][0]['confirmed_at']
    assert snapshot['unresolved_keys']==['slot']
    with pytest.raises(ValueError):select_library_files(snapshot,{'slot':[]},actor='人',basis='依据')


def test_renderer_factory_and_availability_failure(tmp_path):
    from .goods_render import renderer_from_config,LibreOfficeRenderer
    assert isinstance(renderer_from_config({'kind':'libreoffice'}),LibreOfficeRenderer)
    with pytest.raises(ValueError):renderer_from_config({'kind':'unregistered'})
    Document().save(tmp_path/'test.docx')
    class BrokenAvailability:
        def available(self):raise RuntimeError('unavailable')
    assert render_package(tmp_path,BrokenAvailability())['visual_status']=='not_accepted'
