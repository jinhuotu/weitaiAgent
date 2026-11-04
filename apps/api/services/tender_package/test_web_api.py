"""Only isolated module-five connectivity and authorization checks."""
import io,json,zipfile
from types import SimpleNamespace
from hashlib import sha256
from pathlib import Path
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from api.deps import get_current_user
from db.session import get_db
from api.routers import tender_package as routes
from .test_composed import fixture


@pytest.fixture
def client(tmp_path,monkeypatch):
    app=FastAPI();app.include_router(routes.router,prefix='/api/v1')
    user=SimpleNamespace(id=1,username='reviewer',is_superuser=True,roles=[])
    app.dependency_overrides[get_current_user]=lambda:user
    async def database():yield None
    app.dependency_overrides[get_db]=database
    monkeypatch.setattr(routes,'get_settings',lambda:SimpleNamespace(storage_root=str(tmp_path/'storage'),kb_upload_max_bytes=2000000))
    return TestClient(app),user


def package(tmp_path):
    doc,_=fixture(tmp_path);inner=io.BytesIO()
    with zipfile.ZipFile(inner,'w') as z:z.writestr('技术规范_1234-123456789-00001.docx',doc.read_bytes())
    archive=io.BytesIO()
    with zipfile.ZipFile(archive,'w') as z:z.writestr('nested.zip',inner.getvalue())
    return archive.getvalue()


def test_authenticated_upload_to_artifact_chain(client,tmp_path):
    c,user=client;base='/api/v1/tender-package'
    response=c.post(base+'/upload',files={'file':('input.zip',package(tmp_path),'application/zip')})
    assert response.status_code==200,response.text
    state=response.json()['data'];key=state['id']
    def action(name,args=None):
        current=c.get(base+'/'+key).json()['data']
        r=c.post(base+'/'+key+'/actions/'+name,json=dict(revision=current['revision'],arguments=args or {}))
        assert r.status_code==200,r.text
        return c.get(base+'/'+key).json()['data']
    state=action('unpack');assert state['manifest']['file_count']==2
    assert max(f['depth'] for f in state['manifest']['files'])==1
    state=action('classify');tech=next(f for f in state['classified']['files'] if f['role']=='tech_spec')
    state=action('role',dict(storage_path=tech['storage_path'],role='tech_spec',basis='核对正文'))
    assert state['classified']['files'][-1]['manual_override']['actor']=='reviewer'
    code=state['classified']['solidified_id_map'][0]['solidified_id']
    state=action('material',dict(solidified_id=code,name='隔离样本人工确认名称',conductor='铜',basis='合成样本人工确认'))
    state=action('role',dict(storage_path=tech['storage_path'],role='tech_spec',basis='再次核对角色'))
    assert state['classified']['solidified_id_map'][0]['material']=='隔离样本人工确认名称'
    state=action('ssot');assert len(state['ssot']['materials'])==1
    assert state['review']['catalog']['pending_count']==4
    html=c.get(base+'/'+key+'/review-page').json()['data']['html']
    assert 'package-review-request' in html and "fetch(path" not in html
    row=state['review']['catalog']['pending'][0]
    state=action('review',dict(operation='review_rows',arguments=dict(actor='impersonation',basis='实际测试来源',decisions=[dict(solidified_id=row['solidified_id'],target=row['target'],guarantee=row['requirement'],status='响应')])))
    assert state['inputs']['offer_history'][-1]['actor']=='reviewer'
    state=action('generate');assert len(state['result']['technical'])==1
    assert not state['ready_for_submission'] and state['result']['visual_validation']['structure_only']
    download=c.get(base+'/'+key+'/artifacts/0');assert download.status_code==200
    assert download.content[:2]==b'PK'
    user.id=2
    assert c.get(base+'/'+key).status_code==404
    assert c.get(base+'/'+key+'/artifacts/0').status_code==404


def test_stale_revision_malicious_archive_and_permissions(client,tmp_path):
    c,user=client;base='/api/v1/tender-package';z=io.BytesIO()
    with zipfile.ZipFile(z,'w') as f:f.writestr('../escape.txt','no')
    s=c.post(base+'/upload',files={'file':('bad.zip',z.getvalue())}).json()['data'];key=s['id']
    r=c.post(base+'/'+key+'/actions/unpack',json=dict(revision=s['revision'],arguments={}))
    assert r.status_code==422
    assert c.get(base+'/'+key).json()['data']['status']=='failed'
    stale=c.post(base+'/'+key+'/actions/unpack',json=dict(revision=s['revision'],arguments={}))
    assert stale.status_code==422 and '数据已更新' in stale.text
    assert not (tmp_path/'escape.txt').exists()
    user.is_superuser=False
    assert c.get(base).status_code==403


def test_no_legacy_routes_registered(client):
    c,_=client
    paths=set(c.app.openapi()['paths'])
    assert not any('/tenders/' in p for p in paths)
    assert '/api/v1/tender-package/{key}/actions/{operation}' in paths


def test_prepared_handoff_is_owned_and_hash_checked(client,tmp_path):
    from .test_ssot import inputs
    from .ssot import build_ssot,write_ssot
    from .goods_inputs import create_input_session
    from .handoff import prepare
    c,user=client;manifest,final=inputs(tmp_path)
    model=build_ssot(manifest,tmp_path,final);ssot=tmp_path/'ssot.json';write_ssot(model,ssot)
    m=tmp_path/'manifest.json';m.write_text(json.dumps(manifest),encoding='utf-8')
    i=tmp_path/'inputs.json';i.write_text(json.dumps(create_input_session(model)),encoding='utf-8')
    token=prepare(tmp_path/'storage',m,tmp_path,ssot,i)
    r=c.post('/api/v1/tender-package/import-prepared/'+token,json=dict(revision=1,arguments=dict(basis='隔离样本迁移核验')))
    assert r.status_code==200,r.text
    key=r.json()['data']['id'];s=c.get('/api/v1/tender-package/'+key).json()['data']
    assert s['ssot']==model.model_dump() and s['review']['catalog']['pending_count']==4
    assert not s['ready_for_submission'] and s['history'][-1]['actor']=='reviewer'
    prepared=tmp_path/'storage'/'tender-package-imports'/token
    next((prepared/'unpacked').rglob('*.docx')).write_bytes(b'changed')
    r=c.post('/api/v1/tender-package/import-prepared/'+token,json=dict(revision=1,arguments=dict(basis='再次核验')))
    assert r.status_code==422 and '哈希' in r.text
