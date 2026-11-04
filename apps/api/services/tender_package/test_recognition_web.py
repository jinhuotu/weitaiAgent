from copy import deepcopy
import pytest
from .recognition import recognize,describe
from .classify import classify
from .composed import compose_materials
from .web import PackageStore
from .test_composed import fixture
from hashlib import sha256


def manifest(tmp_path,conflict=False):
    path,_=fixture(tmp_path,model='YJLV22' if conflict else 'YJV22',conductor='铜')
    return dict(files=[dict(logical_path='技术规范_1234-123456789-00001.docx',storage_path=path.name,file_type='.docx',role='pending',size=path.stat().st_size,sha256=sha256(path.read_bytes()).hexdigest())])


def test_web_pipeline_matches_cli_and_shared_commitment_role(tmp_path):
    m=manifest(tmp_path)
    p=tmp_path/'promise.txt';p.write_text('廉洁承诺书\n由投标人核对',encoding='utf-8')
    m['files'].append(dict(logical_path=p.name,storage_path=p.name,file_type='.txt',role='pending',sha256=sha256(p.read_bytes()).hexdigest()))
    actual=recognize(m,tmp_path);expected=compose_materials(classify(m,tmp_path),tmp_path)
    assert actual==expected
    assert [f['role'] for f in actual['files']]==['tech_spec','commitment']
    result=describe(actual)
    assert result['id_count']==1 and result['entries'][0]['source_type']=='composed'
    assert result['entries'][0]['selected_sources'][0]['components'][0]['evidence'][0]['row']
    assert m['files'][0]['role']=='pending'


def test_conflict_decision_retains_raw_and_exports_ssot_contract(tmp_path):
    repo=PackageStore(tmp_path/'store',1);s=repo.create('isolated');p=repo.folder(s['id'])
    root=p/'root';root.mkdir();m=manifest(root,True);s.update(root='root',manifest=m)
    s=repo.apply(p,s,'classify',{},'自动识别')
    assert describe(s['classified'])['pending_ids']==['1234-123456789-00001']
    original=deepcopy(s['classified']['composition_history']);s['revision']=1;repo.save(p,s)
    args=dict(solidified_id='1234-123456789-00001',model='YJV22',conductor='铜',name='隔离样本铜芯电缆名称',basis='仅合成样本明确裁决')
    with pytest.raises(ValueError):repo.action(s['id'],s['revision'],'material',dict(args,model='YJLV22'),'测试确认人')
    s=repo.latest(s['id']);s=repo.action(s['id'],s['revision'],'material',args,'测试确认人')
    assert s['classified']['composition_history']==original
    event=s['classified']['material_source_ledger'][-1]
    assert event['confirmed_parameters']==dict(model='YJV22',conductor='铜') and event['confirmed_at']
    s=repo.action(s['id'],s['revision'],'role',dict(storage_path='random.docx',role='tech_spec',basis='再次核对角色'),'测试确认人')
    assert describe(s['classified'])['entries'][0]['source_type']=='manual'
    s=repo.action(s['id'],s['revision'],'material_export',{},'测试确认人')
    assert s['mapping']['payload']['schema_version']=='tender-package-material-map/1'
    assert repo.latest(s['id'])['mapping']==s['mapping']
    first_id=s['classified']['material_source_ledger'][0]['record_id']
    s=repo.action(s['id'],s['revision'],'material',dict(args,name='隔离样本更正后的铜芯名称'),'测试确认人')
    assert describe(s['classified'])['entries'][0]['needs_confirmation']
    s=repo.action(s['id'],s['revision'],'material_retire',dict(record_id=first_id,basis='明确停用旧名称来源，保留历史'),'测试确认人')
    old=s['classified']['material_source_ledger'][0]
    assert old['active'] is False and old['corrections'][-1]['confirmed_at']
    assert describe(s['classified'])['entries'][0]['material_name']=='隔离样本更正后的铜芯名称'
