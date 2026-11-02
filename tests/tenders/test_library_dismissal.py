"""Canonical library view and virtual opt-out, without touching source DOCX."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from api.services.tenders import library_kb
from api.services.tenders.library_catalog import SlotCatalog
from api.services.tenders.material_identity import material_key_aliases
from api.services.tenders.placeholders import DEFAULT_SLOTS, collect_slots, this_bid_keys
from api.services.tenders.schema import PlaceholderItem
from common.errors import AppError
from db.models.knowledge import KnowledgeBase
from db.models.tender_library import TenderLibraryDismissal


def slot(key, title):
    return PlaceholderItem(key=key, title=title)


@pytest.mark.parametrize('variant', ['营业执照', '企业法人营业执照副本扫描件', '附：本公司营业执照复印件',
                                    '营业执照（副本）复印件（加盖公章）'])
def test_generated_custom_and_standard_keys_make_one_slot(variant):
    items = [slot('mabcdef123', variant), slot('material_123', variant)]
    selected = collect_slots(items, catalog=[slot('license', '营业执照')],
                             include_keys=['mabcdef123', 'material_123', 'license'])
    assert [item.key for item in selected] == ['license']


def test_unrelated_companies_people_and_periods_do_not_merge():
    items = [slot('m1', '甲公司营业执照'), slot('m2', '乙公司营业执照'),
             slot('m3', '法定代表人张三身份证'), slot('m4', '法定代表人李四身份证'),
             slot('m5', '2023年财务报表'), slot('m6', '2024年财务报表')]
    aliases = material_key_aliases([*DEFAULT_SLOTS, *items])
    assert all(aliases[item.key] == item.key for item in items)


def test_real_parent_and_file_ids_survive_canonical_view():
    rows = [dict(key='license', title='营业执照', docId='', fileCount=0, files=[]),
            dict(key='mreal', title='营业执照副本扫描件', docId='parent-1', fileCount=1,
                 files=[dict(fileId='original-1', fileName='scan.png')])]
    merged = library_kb._merged_library_rows(rows)
    assert list(merged) == ['license']
    assert merged['license']['parentKey'] == 'mreal'
    assert merged['license']['docId'] == 'parent-1'
    assert merged['license']['files'][0]['fileId'] == 'original-1'


class AsyncSessionAdapter:
    """Real SQLite persistence, exposing just the async methods under test."""
    def __init__(self, session):
        self.session = session

    async def execute(self, query):
        return self.session.execute(query)

    def add(self, row):
        self.session.add(row)

    async def commit(self):
        self.session.commit()


@pytest.fixture
def database(tmp_path, monkeypatch):
    engine = create_engine('sqlite:///' + (tmp_path / 'library.sqlite').as_posix())
    KnowledgeBase.__table__.create(engine)
    TenderLibraryDismissal.__table__.create(engine)
    with Session(engine) as session:
        session.add(KnowledgeBase(id=1, public_id='tenderlib01', name='资料库'))
        session.commit()
    monkeypatch.setattr(library_kb, 'ensure_library_base', AsyncMock(return_value=SimpleNamespace(id=1)))
    monkeypatch.setattr(library_kb, 'list_library_items', AsyncMock(return_value=[]))
    monkeypatch.setattr(library_kb, 'list_slot_files', lambda key: [])
    yield engine
    engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('key,title', [('license', '营业执照'), ('material_unseen_123', '陌生交付物证明')])
async def test_dismissal_survives_new_session_refresh_and_recognition(database, key, title):
    with Session(database) as session:
        db = AsyncSessionAdapter(session)
        assert await library_kb.dismiss_library_item(db, key) == {'key': key, 'dismissed': True}
        await library_kb.dismiss_library_item(db, key)  # idempotent
    with Session(database) as session:
        db = AsyncSessionAdapter(session)
        catalog = await library_kb.catalog_placeholders(db)
        assert key in catalog.dismissed_keys
        assert not collect_slots([slot(key, title)], catalog=catalog, include_keys=[key])
        status = await library_kb.list_slots_status_kb(db, [slot(key, title)], include_keys=[key])
        assert status == []
        # Parsing a later invitation must not recreate an empty real parent.
        assert await library_kb.persist_parsed_materials(db, [slot(key, title)]) == []


@pytest.mark.asyncio
async def test_ignored_canonical_key_also_hides_new_alias(database):
    with Session(database) as session:
        db = AsyncSessionAdapter(session)
        await library_kb.dismiss_library_item(db, 'license')
        catalog = await library_kb.catalog_placeholders(db)
        assert not collect_slots([slot('mnewalias', '营业执照副本')], catalog=catalog,
                                 include_keys=['mnewalias'])


@pytest.mark.asyncio
@pytest.mark.parametrize('has_file', [False, True])
async def test_real_items_cannot_be_dismissed(database, monkeypatch, has_file):
    rows = [dict(key='mreal', title='营业执照副本', docId='real-parent', fileCount=int(has_file), files=[])]
    monkeypatch.setattr(library_kb, 'list_library_items', AsyncMock(return_value=rows))
    with Session(database) as session:
        db = AsyncSessionAdapter(session)
        with pytest.raises(AppError) as error:
            await library_kb.dismiss_library_item(db, 'license')
        assert error.value.status_code == 409
        assert not await library_kb.dismissed_library_keys(db)


def test_explicit_upload_not_hidden_by_old_dismissal():
    catalog = SlotCatalog([slot('mreal', '营业执照副本')], dismissed_keys=['license'], real_keys=['mreal'])
    assert [s.key for s in collect_slots(catalog=catalog, include_keys=['license'])] == ['license']


@pytest.mark.asyncio
async def test_default_empty_entries_are_not_reseeded(database, monkeypatch):
    monkeypatch.setattr(library_kb, '_list_docs', AsyncMock(return_value=[]))
    with Session(database) as session:
        db = AsyncSessionAdapter(session)
        await library_kb._seed_defaults_if_empty(db, SimpleNamespace(id=1))
        assert not session.new


@pytest.mark.asyncio
async def test_canonical_media_retains_all_real_alias_files(tmp_path, monkeypatch):
    paths = [tmp_path / 'first.png', tmp_path / 'second.png']
    for n, path in enumerate(paths):
        path.write_bytes(bytes([n + 1]))
    parents = [SimpleNamespace(public_id=key, parent_id=None, name=title, tags=['slot:' + key])
               for key, title in [('license', '营业执照'), ('mreal', '企业营业执照副本')]]
    monkeypatch.setattr(library_kb, 'get_base_by_public_id', AsyncMock(return_value=SimpleNamespace(id=1)))
    monkeypatch.setattr(library_kb, '_list_docs', AsyncMock(return_value=parents))
    monkeypatch.setattr(library_kb, '_files_of', lambda parent, children: [paths[0 if parent.public_id == 'license' else 1]])
    monkeypatch.setattr(library_kb, 'list_slot_files', lambda key: [])
    media = await library_kb.resolve_attachments(None, [slot('license', '营业执照')])
    assert media['license'] == paths


@pytest.mark.asyncio
async def test_real_parent_delete_still_uses_existing_record_and_file_cleanup(monkeypatch):
    parent = SimpleNamespace(public_id='real-parent')
    get_parent = AsyncMock(return_value=(parent, []))
    delete = AsyncMock(return_value=True)
    db = SimpleNamespace(commit=AsyncMock())
    cleared = []
    monkeypatch.setattr(library_kb, '_get_parent', get_parent)
    monkeypatch.setattr(library_kb, 'ensure_library_base', AsyncMock(return_value=SimpleNamespace(public_id='tenderlib01')))
    monkeypatch.setattr(library_kb, 'delete_document_record', delete)
    monkeypatch.setattr(library_kb, 'clear_slot', cleared.append)
    assert await library_kb.delete_library_item(db, 'mreal') == {'key': 'mreal', 'deleted': True}
    delete.assert_awaited_once_with(db, base_public_id='tenderlib01', doc_public_id='real-parent')
    assert cleared == ['mreal']
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_dismiss_route_is_post_and_requires_manage_permission(monkeypatch):
    from api.routers import tenders

    routes = [route for route in tenders.router.routes if getattr(route, 'path', '').endswith('/library/dismissed')]
    assert len(routes) == 1 and routes[0].methods == {'POST'}
    permission = AsyncMock()
    dismiss = AsyncMock(return_value={'key': 'virtual', 'dismissed': True})
    monkeypatch.setattr(tenders.kb_access, 'require_tender_lib_manage', permission)
    monkeypatch.setattr(tenders, 'dismiss_library_item', dismiss)
    db, user = object(), object()
    await tenders.tenders_dismiss_library_item(tenders.LibraryDismissIn(key='virtual'), db, user)
    permission.assert_awaited_once_with(db, user)
    dismiss.assert_awaited_once_with(db, 'virtual')


def test_selected_keys_follow_canonical_slots_and_virtual_opt_out():
    items = [slot('mlicense', '营业执照副本')]
    required, include = this_bid_keys(extra=items, catalog=[], required_keys=['mlicense'],
                                     include_keys=['mlicense'])
    assert 'license' in required and 'license' in include
    assert 'mlicense' not in required and 'mlicense' not in include
    catalog = SlotCatalog([], dismissed_keys=['license'])
    required, include = this_bid_keys(extra=items, catalog=catalog,
                                     required_keys=['mlicense'], include_keys=['mlicense'])
    assert 'license' not in required and 'license' not in include


@pytest.mark.asyncio
async def test_post_http_persists_dismissal(database, monkeypatch):
    import httpx
    from fastapi import FastAPI
    from api.deps import get_current_user
    from api.routers import tenders
    from db.session import get_db

    app = FastAPI()
    app.include_router(tenders.router, prefix='/api/v1')
    permission = AsyncMock()
    monkeypatch.setattr(tenders.kb_access, 'require_tender_lib_manage', permission)
    with Session(database) as session:
        db = AsyncSessionAdapter(session)

        async def session_dependency():
            yield db

        app.dependency_overrides[get_db] = session_dependency
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
            response = await client.post('/api/v1/tenders/library/dismissed', json={'key': 'material_http'})
        assert response.status_code == 200
        assert 'material_http' in await library_kb.dismissed_library_keys(db)
        permission.assert_awaited_once()
