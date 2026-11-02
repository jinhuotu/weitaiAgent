"""Recognition never persists parents; uploads always use a standard container."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from api.services.tenders import library_kb
from api.services.tenders.material_identity import standard_material_key
from api.services.tenders.material_intents import structural_demands, source_units
from api.services.tenders.schema import BidBrief, OutlineItem, PlaceholderItem


@pytest.mark.parametrize('title,key', [('营业执照副本','license'), ('基本存款账户信息','bank_permit'),
    ('投标保证金电子保函凭证（备选用）','bond'), ('保证金缴纳凭证（本次金额）','bond'),
    ('社保缴纳证明','social'), ('企业资质证书','qualification'), ('质量管理体系认证证书','iso'),
    ('财务审计报告','finance'), ('类似项目合同及发票','perf'), ('产品检测报告','product')])
def test_semantic_storage_mapping_does_not_use_opaque_key(title, key):
    assert standard_material_key(PlaceholderItem(key='material_unseen_123', title=title)) == key


@pytest.mark.asyncio
async def test_recognition_is_memory_only_for_familiar_and_unseen_requirements(monkeypatch):
    monkeypatch.setattr(library_kb, 'dismissed_library_keys', AsyncMock(return_value=set()))
    db = SimpleNamespace(add=lambda _: pytest.fail('recognition cannot create a database record'),
                         commit=AsyncMock(side_effect=AssertionError('recognition cannot commit')))
    items = [PlaceholderItem(key='mnew', title='营业执照副本'),
             PlaceholderItem(key='material_unknown', title='陌生交付证明', attachToSection='专项资料')]
    result = await library_kb.persist_parsed_materials(db, items)
    assert [item.key for item in result] == ['license', 'material_unknown']
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_catalog_and_requirement_count_do_not_depend_on_empty_parent_rows(monkeypatch):
    rows = [dict(key='mempty'+str(n), title='空容器'+str(n), docId='empty'+str(n), fileCount=0) for n in range(271)]
    monkeypatch.setattr(library_kb, 'list_library_items', AsyncMock(return_value=rows))
    monkeypatch.setattr(library_kb, 'dismissed_library_keys', AsyncMock(return_value=set()))
    before = await library_kb.catalog_placeholders(None)
    library_kb.list_library_items.return_value = []
    after = await library_kb.catalog_placeholders(None)
    assert [item.model_dump() for item in before] == [item.model_dump() for item in after]
    brief = BidBrief(outlineItems=[OutlineItem(id='qual', title='资格审查资料', kind='company')])
    units = source_units(brief, '资格审查资料\n附：营业执照复印件。\n附：基本账户开户证明扫描件。\n应提供《陌生交付证明》。')
    first = structural_demands(brief, units, before)
    second = structural_demands(brief, units, after)
    assert first and [(item.title,item.slotKey) for item in first] == [(item.title,item.slotKey) for item in second]


@pytest.mark.asyncio
async def test_explicit_create_reuses_standard_parent_instead_of_new_custom_key(monkeypatch):
    existing = dict(key='license', title='营业执照', docId='real', fileCount=1)
    monkeypatch.setattr(library_kb, 'ensure_library_base', AsyncMock(return_value=SimpleNamespace(id=1)))
    monkeypatch.setattr(library_kb, 'list_library_items', AsyncMock(return_value=[existing]))
    db = SimpleNamespace(add=lambda _: pytest.fail('duplicate container created'))
    assert await library_kb.create_library_item(db, title='营业执照副本', key='mnew') is existing
