"""Only wizard projections/action connectivity; no full suite or real task writes."""
from copy import deepcopy
import json
from .wizard import build_wizard, Issue, COLLECTORS
from .test_web_api import client, package


def test_wizard_read_only_auto_steps_and_action_roundtrip(client,tmp_path):
    c,user=client;base='/api/v1/tender-package'
    state=c.post(base+'/upload',files={'file':('input.zip',package(tmp_path))}).json()['data']
    key=state['id'];repo=__import__('api.routers.tender_package',fromlist=['store']).store(user)
    folder=repo.folder(key)
    def wizard():
        r=c.get(f'{base}/{key}/wizard');assert r.status_code==200,r.text
        return r.json()['data']
    before={p.name:p.read_bytes() for p in folder.glob('state-*')}
    w=wizard();assert w['can_auto_continue'] and w['next_action']=='unpack'
    assert {p.name:p.read_bytes() for p in folder.glob('state-*')}==before
    for stage in ('unpack','classify'):
        w=wizard();assert w['next_action']==stage and w['can_auto_continue']
        r=c.post(f'{base}/{key}/actions/{stage}',json=dict(revision=w['revision'],arguments={}))
        assert r.status_code==200,r.text
    w=wizard();assert w['next_action']=='ssot'
    # An ordinary unknown file remains an issue until explicitly assigned a role.
    state=repo.latest(key)
    f=next(f for f in state['classified']['files'] if f['role']=='tech_spec')
    r=c.post(f'{base}/{key}/actions/role',json=dict(revision=w['revision'],arguments=dict(storage_path=f['storage_path'],role='other',basis='隔离测试人工分类')))
    assert r.status_code==200,r.text
    w=wizard();resolved=[i for i in w['issues'] if i['type']=='role_ambiguity']
    assert resolved and resolved[0]['status']=='resolved'
    assert repo.latest(key)['role_overrides'][f['storage_path']]['actor']=='reviewer'
    assert c.post(f'{base}/{key}/actions/role',json=dict(revision=1,arguments={})).status_code==422
    user.id=2
    assert c.get(f'{base}/{key}/wizard').status_code==404
    user.id=1;user.is_superuser=False
    assert c.get(f'{base}/{key}/wizard').status_code==403


def sample():
    return dict(id='a'*32,revision=2,stage='ssot',status='review_required',
        manifest={'files':[]},classified={'files':[],'solidified_id_map':[]},
        recognition={'entries':[]},inputs={'package_history':[]},
        ssot=dict(lots=[dict(key='lot',fields={'lot_name':{'value':'隔离分标'}},row_sources=[])],
            materials=[dict(solidified_id='generic-id',material_mapping={'material_name':'样本物料'})],documents=[],pending=[],conflicts=[]),
        review=dict(packages=None,price_history=[],price_targets=[dict(document_id='d',table=1,row=2,column=3,title='单价',
            source={'locator':'table:1/row:2','quote':'原始要求'},source_document={'logical_path':'nested.zip/报价.docx'})],
            catalog={'pending':[]},business={'items':[]}))


def test_contract_stability_exact_targets_no_defaults_and_resolution():
    s=sample();original=deepcopy(s);w=build_wizard(s)
    assert s==original and not w['can_auto_continue']
    assert [i['id'] for i in w['issues']]==[i['id'] for i in build_wizard(s)['issues']]
    for i in w['issues']:
        Issue.model_validate(i)
        assert i['action']['default'] is None
    price=next(i for i in w['issues'] if i['type']=='price_missing')
    args=price['target_action']['arguments']['arguments']
    assert [args[k] for k in ('document_id','table','row','column')]==['d',1,2,3]
    assert price['action']['schema']['properties']['amount']['minimum']==0
    assert 'amount' in price['action']['schema']['required']
    assert price['unavailable_reason'] and price['action']['schema']['properties']['package_key']['enum']==[]
    s['inputs']['package_history']=[{'packages':[dict(key='pkg',package_code='一包')]}]
    s['review']['packages']=s['inputs']['package_history'][0]['packages']
    s['review']['price_history']=[dict(package_key='pkg',document_id='d',table=1,row=2,column=3)]
    s['library']={'loaded':True}
    w=build_wizard(s)
    assert next(i for i in w['issues'] if i['type']=='price_missing')['status']=='resolved'
    assert next(i for i in w['issues'] if i['type']=='missing_field')['status']=='resolved'
    assert w['can_auto_continue'] and w['next_action']=='generate'
    s['result']={'price':[dict(source_document='d',proof={'rejected':[dict(table=1,row=2,column=3,reason='合并单元格需核对')]})]}
    w=build_wizard(s);p=next(i for i in w['issues'] if i['type']=='price_missing')
    assert p['status']=='deferred' and p['unavailable_reason']


def test_business_and_conflicting_parameters_remain_explicit_and_traceable():
    s=sample();s['library']={'loaded':True,'unresolved_keys':[]}
    s['review']['business']={'items':[dict(key='p/s',kind='material',title='资格材料',sources=[dict(document_id='d',locator='paragraph:2',quote='须提供材料')],
         candidates=[dict(document_id='real',path='C:/private/credential.docx')],review=None)]}
    s['review']['catalog']['pending']=[dict(solidified_id='generic-id',target='d:table:1/row:3',label='参数',review_reason='原文与已有确认不同',
        conflict={'candidates':[]},source={'document_id':'d','locator':'table:1/row:3','quote':'原文参数'},source_document={'logical_path':'技术规范.docx'})]
    w=build_wizard(s);b=next(i for i in w['issues'] if i['type']=='business_missing')
    assert b['target_action']['arguments']['operation']=='business'
    assert b['action']['schema']['properties']['decision']['enum']==['real_files']
    assert b['action']['schema']['properties']['document_ids']['items']['enum']==['real']
    assert 'C:/' not in json.dumps(w,ensure_ascii=False)
    r=next(i for i in w['issues'] if i['type']=='material_conflict')
    assert r['action']['schema']['properties']['status']['enum']==['响应','偏离']
    assert r['action']['default'] is None and r['evidence'][0]['quote']=='原文参数'
    s['review']['business']['items'][0]['review']={'actor':'human','basis':'有依据'}
    assert next(i for i in build_wizard(s)['issues'] if i['type']=='business_missing')['status']=='resolved'


def test_registry_and_failed_stage_do_not_silently_continue():
    s={'id':'b'*32,'revision':1,'status':'failed'}
    w=build_wizard(s);assert w['retry_required'] and not w['can_auto_continue']
    assert w['retry_action'] is None
    s.update(stage='unpack',archive='example.zip');assert build_wizard(s)['retry_action']=='unpack'
    s['status']='processing';assert build_wizard(s)['retry_action'] is None
    assert {'roles','materials','packages','prices','parameters','business','uncovered'}<=set(COLLECTORS)
    s=sample();s['result']={'warnings':[{'type':'ssot_pending','message':'SSOT待确认：fields.private_name'}]}
    assert 'fields.private_name' not in json.dumps(build_wizard(s),ensure_ascii=False)
    assert build_wizard(s)['result']['visual_label']=='仅结构检查通过，视觉未验收'
    s['result']['visual_validation']={'documents':[{'status':'rendered_pending_review','pages':[{}]}]}
    assert build_wizard(s)['result']['visual_label']=='已渲染，待逐页人工核验'
    s['result']['visual_validation']['visual_status']='accepted'
    assert build_wizard(s)['result']['visual_label']=='视觉核验记录已通过'


def test_parameter_issue_keeps_identity_and_resolved_status_after_review():
    s=sample();row=dict(solidified_id='generic-id',target='d:row:2',label='参数名称',review_reason='来源不同',
        conflict={'candidates':[]},source={'quote':'原文'},source_document={'logical_path':'技术规范.docx'})
    s['review']['catalog']['pending']=[row]
    before=next(i for i in build_wizard(s)['issues'] if i['id'].startswith('parameter:'))
    assert before['status']=='open' and build_wizard(s)['stage']=='review'
    reviewed=deepcopy(row);reviewed.pop('conflict');reviewed.pop('review_reason');reviewed['guarantee']='已确认的真实值'
    s['review']['catalog'].update(pending=[],automatic=[reviewed])
    after=next(i for i in build_wizard(s)['issues'] if i['id'].startswith('parameter:'))
    assert before['id']==after['id'] and after['status']=='resolved'


def test_material_conflict_action_keeps_history_and_issue_identity(client,tmp_path):
    import io,zipfile
    from .test_recognition_web import manifest
    c,_=client;base='/api/v1/tender-package'
    m=manifest(tmp_path,conflict=True);f=m['files'][0];archive=io.BytesIO()
    with zipfile.ZipFile(archive,'w') as z:z.write(tmp_path/f['storage_path'],f['logical_path'])
    state=c.post(base+'/upload',files={'file':('conflict.zip',archive.getvalue())}).json()['data'];key=state['id']
    for op in ('unpack','classify'):
        rev=c.get(base+'/'+key).json()['data']['revision']
        r=c.post(f'{base}/{key}/actions/{op}',json=dict(revision=rev,arguments={}))
        assert r.status_code==200,r.text
    before=c.get(base+'/'+key).json()['data']
    w=c.get(f'{base}/{key}/wizard').json()['data'];item=next(i for i in w['issues'] if i['type']=='material_conflict')
    assert item['status']=='open' and not w['can_auto_continue']
    quotes=' '.join(e['quote'] for e in item['evidence']);assert 'YJLV22' in quotes and '铜' in quotes
    assert item['target_action']['operation']=='material'
    args=dict(solidified_id=before['classified']['solidified_id_map'][0]['solidified_id'],
              model='YJV22',conductor='铜',name='隔离样本铜芯名称',basis='仅合成样本人工裁决')
    r=c.post(f'{base}/{key}/actions/material',json=dict(revision=w['revision'],arguments=args));assert r.status_code==200,r.text
    after=c.get(base+'/'+key).json()['data'];assert after['classified']['composition_history']==before['classified']['composition_history']
    event=after['classified']['material_source_ledger'][-1];assert event['actor']=='reviewer' and event['confirmed_at']
    resolved=next(i for i in c.get(f'{base}/{key}/wizard').json()['data']['issues'] if i['id']==item['id'])
    assert resolved['status']=='resolved'
