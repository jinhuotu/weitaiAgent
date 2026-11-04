from datetime import date, timedelta
from types import SimpleNamespace
import pytest
from api.services.tenders.expiry_check import collect_expiry_warnings

@pytest.mark.parametrize('days,level', [(-1,'danger'),(0,'danger'),(30,'danger'),(31,'warning'),(90,'warning'),(91,None)])
def test_boundaries(tmp_path,days,level):
    p=tmp_path/'file';p.write_text('original')
    doc=SimpleNamespace(public_id='d',name='证件',expire_at=date(2026,1,1)+timedelta(days=days),is_perpetual=False)
    report={'matched':[dict(key='k',title='材料',fileCount=1)]}
    result=collect_expiry_warnings(report,{'k':[p]},[(doc,p)],today=date(2026,1,1))
    assert ([w['level'] for w in result])==([level] if level else [])
    assert p.read_text()=='original'
    if result: assert result[0]['remaining_days']==days

def test_only_actual_selected_files(tmp_path):
    files=[]
    for i in range(3):
        p=tmp_path/str(i);p.touch();files.append(p)
    docs=[(SimpleNamespace(public_id=str(i),name=str(i),expire_at=date(2000,1,1),is_perpetual=False),p) for i,p in enumerate(files)]
    result=collect_expiry_warnings({'matched':[dict(key='k',fileCount=2),dict(key='empty',fileCount=0)]}, {'k':files[:2],'empty':files[2:]},docs)
    assert [w['document_id'] for w in result]==['0','1']
    assert collect_expiry_warnings({'matched':[]},{'k':files},docs)==[]

def test_perpetual_unknown_and_project(tmp_path,monkeypatch):
    from api.services.tenders import expiry_check
    p=tmp_path/'f';p.touch()
    doc=SimpleNamespace(public_id='d',name='d',expire_at=None,is_perpetual=False)
    args=({'matched':[dict(key='k',fileCount=1)]},{'k':[p]},[(doc,p)])
    monkeypatch.setattr(expiry_check,'get_settings',lambda: SimpleNamespace(expire_unknown_warn=False))
    assert collect_expiry_warnings(*args)==[]
    monkeypatch.setattr(expiry_check,'get_settings',lambda: SimpleNamespace(expire_unknown_warn=True))
    assert collect_expiry_warnings(*args)[0]['level']=='info'
    doc.is_perpetual=True
    assert collect_expiry_warnings(*args)==[]
    doc.is_perpetual=False;doc.expire_at=date(2030,1,1)
    assert collect_expiry_warnings(*args,today=date(2026,1,1))==[]
    assert collect_expiry_warnings(*args,today=date(2026,1,1),project_end=date(2031,1,1))[0]['expire_status']=='project_expiry'

@pytest.mark.asyncio
async def test_check_failure_is_nonblocking(monkeypatch):
    from unittest.mock import AsyncMock
    from api.services.tenders import library_kb
    from api.services.tenders.expiry_check import generation_expiry_warnings
    monkeypatch.setattr(library_kb,'get_base_by_public_id',AsyncMock(side_effect=RuntimeError('unavailable')))
    assert await generation_expiry_warnings(None,{'matched':[]},{})==[]


def test_warning_step_is_after_document_builder():
    import inspect
    from api.services.tenders.records import _prepare_and_generate
    source=inspect.getsource(_prepare_and_generate)
    assert source.index('warnings.extend(await generation_expiry_warnings') > source.index('generated = generate_bid')
    from api.services.tenders import document, assemble
    assert 'generation_expiry_warnings' not in inspect.getsource(document)
    assert 'generation_expiry_warnings' not in inspect.getsource(assemble)

