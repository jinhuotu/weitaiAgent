from datetime import date, timedelta
from types import SimpleNamespace
import pytest
from api.services.knowledge.expiry import extract_expiry, normalize_date, apply_expiry, expiry_fields

@pytest.mark.parametrize('raw', ['2030-06-30','2030 年 06 月 30 日','20300630','2030.06.30','２０３０／０６／３０'])
def test_normalize(raw):
    assert normalize_date(raw) == date(2030,6,30)

@pytest.mark.parametrize('title', ['身份证','营业执照','安全生产考核合格证B证','考核合格证C证','注册资格证','执业资格证','职业资格证','许可证','体系认证','保函'])
@pytest.mark.parametrize('text', ['有效期至2030-06-30','有效期限 2020年06月30日 - 2030年06月30日','营业期限 自20200630至20300630'])
def test_patterns(title,text):
    assert extract_expiry(title+'\n'+text) == (date(2030,6,30),False)

@pytest.mark.parametrize('word',['长期','永久','无固定期限'])
def test_perpetual(word):
    assert extract_expiry('有效期限2020-01-01至'+word) == (None,True)

@pytest.mark.parametrize('text',['产品合格证','出厂合格证','签发日期2020-01-01','有效期至2030-02-30','有效期限自2020-01-01'])
def test_unknown(text):
    assert extract_expiry(text)==(None,False)

def test_no_empty_overwrite():
    doc=SimpleNamespace(expire_at=date(2030,1,1),is_perpetual=False)
    apply_expiry(doc,'无日期')
    assert doc.expire_at==date(2030,1,1)

@pytest.mark.parametrize('days,state,level',[(-1,'expired','danger'),(0,'expiring','danger'),(30,'expiring','danger'),(31,'expiring','warning'),(90,'expiring','warning'),(91,'normal','none')])
def test_states(days,state,level):
    doc=SimpleNamespace(expire_at=date(2026,1,1)+timedelta(days=days),is_perpetual=False)
    result=expiry_fields(doc,today=date(2026,1,1))
    assert (result['expire_status'],result['expire_level'])==(state,level)

def test_unknown_perpetual():
    assert expiry_fields(SimpleNamespace())['expire_status']=='unknown'
    assert expiry_fields(SimpleNamespace(is_perpetual=True,expire_at=date(2000,1,1)))['expire_level']=='none'

@pytest.mark.asyncio
@pytest.mark.parametrize('end,perpetual',[(None,False),(None,True),(date(2020,1,1),False),(date(2030,1,1),False)])
async def test_manual_endpoint(monkeypatch,end,perpetual):
    from unittest.mock import AsyncMock
    from api.routers import knowledge as router
    from api.schemas.knowledge import UpdateExpiryRequest
    from api.services.knowledge import ingest
    doc=SimpleNamespace(expire_at=None,is_perpetual=False)
    require=AsyncMock(return_value=SimpleNamespace(id=12))
    monkeypatch.setattr(router.kb_access,'require_base',require)
    monkeypatch.setattr(ingest,'_get_doc_in_base',AsyncMock(return_value=doc))
    monkeypatch.setattr(ingest,'to_kb_item',lambda d: expiry_fields(d))
    db=SimpleNamespace(commit=AsyncMock())
    result=await router.documents_update_expiry('doc',UpdateExpiryRequest(baseId='base',expire_at=end,is_perpetual=perpetual),db,SimpleNamespace())
    assert result['data']['item']['is_perpetual'] is perpetual
    assert doc.expire_at == (None if perpetual else end)
    db.commit.assert_awaited_once()


def test_configurable_thresholds(monkeypatch):
    from api.services.knowledge import expiry
    monkeypatch.setattr(expiry,'get_settings',lambda: SimpleNamespace(expire_warn_days=15,expire_danger_days=5))
    doc=SimpleNamespace(expire_at=date(2026,1,11),is_perpetual=False)
    assert expiry_fields(doc,today=date(2026,1,1))['expire_level']=='warning'
    assert expiry_fields(doc,today=date(2025,12,1))['expire_status']=='normal'
