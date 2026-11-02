import asyncio
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from docx import Document
from docx.oxml.ns import qn
from PIL import Image

from api.services.tenders import material_store as store
from api.services.tenders.schema import BidBrief, MaterialUse, OutlineItem
from common.errors import AppError

INVITATION = '123456abcdef'
OTHER = 'abcdef123456'


@pytest.fixture
def archive(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'tender_assets_dir', lambda: tmp_path)
    parser = AsyncMock(return_value=SimpleNamespace(text='本项目采购需求及证明材料'))
    monkeypatch.setattr('api.services.knowledge.parsers.extract_text_from_file', parser)
    monkeypatch.setattr(store, 'existing_sections', lambda b: [i.title for i in b.outlineItems])
    return tmp_path, parser


def upload(category='personnel', subtype='简历', entity='人员甲', extension='.png', data=None):
    if data is None:
        out = BytesIO()
        Image.new('RGB', (120, 80), 'white').save(out, format='PNG')
        data = out.getvalue()
    return asyncio.run(store.upload_material(1, INVITATION, category,
                       filename='资料' + extension, data=data, subtype=subtype, entity=entity, bidder_name="测试投标有限公司"))


def bid(item, enabled=True, **kwargs):
    return BidBrief(bidderName='测试投标有限公司', generateVolume='full', invitationId=INVITATION,
                    outlineItems=[OutlineItem(id='people', title='项目管理机构', kind='company')],
                    materialSelections={item['category']: MaterialUse(enabled=enabled,
                       fileIds=[item['id']], targetSection='项目管理机构', **kwargs)})


@pytest.mark.parametrize('category', list(store.CATEGORIES))
def test_each_new_category_is_an_independent_default_off_archive(archive, category):
    item = upload(category, store.CATEGORIES[category][2][0])
    assert item['enabled'] is False
    assert item['parseStatus'] == 'parsed'
    files = store.list_materials(1, INVITATION)['files']
    assert [f['id'] for f in files] == [item['id']]
    assert all(not c['enabled'] for c in store.catalog())
    assert not (archive[0] / 'slots').exists()
    assert not (archive[0] / 'tech-plan-sources').exists()


def test_owner_and_project_isolation(archive):
    project = upload('survey', '踏勘')
    with pytest.raises(AppError):
        store.material_path(2, INVITATION, 'survey', project['id'])
    with pytest.raises(AppError):
        store.material_path(1, OTHER, 'survey', project['id'])
    library = upload()
    assert store.material_path(1, OTHER, 'personnel', library['id'])[0].is_file()


@pytest.mark.parametrize('category,invitation,file_id', [('bad', INVITATION, 'a'*32),
  ('survey', '../outside', 'a'*32), ('personnel', INVITATION, '../secret')])
def test_invalid_identifiers_cannot_escape_storage(archive, category, invitation, file_id):
    with pytest.raises(AppError):
        store.material_path(1, invitation, category, file_id)


def test_raw_text_is_never_overwritten_by_truncated_parse_cache(archive):
    raw = ('原版资料' * 18000).encode()
    archive[1].return_value = SimpleNamespace(text=raw.decode())
    item = upload(extension='.txt', data=raw)
    path, _ = store.material_path(1, INVITATION, 'personnel', item['id'])
    assert path.read_bytes() == raw
    assert len((path.parent / (item['id'] + '.txt')).read_text(encoding='utf-8')) == 60000
    store.delete_material(1, INVITATION, 'personnel', item['id'])
    assert not list(path.parent.iterdir())


def test_parse_failure_retains_archive_and_is_explicit(archive):
    archive[1].side_effect = ValueError('bad scan')
    item = upload()
    assert item['parseStatus'] == 'parse_failed'
    assert store.material_path(1, INVITATION, 'personnel', item['id'])[0].is_file()


def test_switch_off_is_an_identity_operation_even_with_selected_ids(archive):
    item = upload()
    b = bid(item, enabled=False)
    slots, media = [], {}
    before = b.model_dump()
    a, m, notes, refs = store.generation_inputs(1, b, slots, media)
    assert a is slots and m is media and not notes and not refs
    assert b.model_dump() == before


def test_missing_target_does_not_create_outline_or_placeholder(archive):
    item = upload()
    b = bid(item)
    b.materialSelections['personnel'].targetSection = '不存在的章节'
    original = b.model_dump()
    slots, media, notes, refs = store.generation_inputs(1, b, [], {})
    assert slots == [] and media == {} and refs == []
    assert notes and b.model_dump() == original
    assert store.preflight(1, b)['selected'][0]['issues']


def test_triplets_are_checked_per_project_not_across_projects(archive):
    upload('performance', '合同', '项目甲')
    upload('performance', '中标通知书', '项目乙')
    upload('performance', '验收报告', '项目乙')
    check = store.preflight(1, BidBrief(invitationId=INVITATION))
    missing = {i['entity']: i['missing'] for i in check['missing']}
    assert missing == {'项目甲': ['中标通知书', '验收报告'], '项目乙': ['合同']}


def test_reference_ids_do_not_leak_into_persisted_brief_or_disabled_generations(archive, monkeypatch):
    item = upload('geology', '地勘', extension='.docx')
    b = bid(item, mode='reference')
    b.autoDraftTechPlan = True
    monkeypatch.setattr('api.services.tenders.source_match.match_source',
                        lambda *a, **k: SimpleNamespace(status='matched'))
    monkeypatch.setattr('api.services.tenders.source_match.require_source_match', lambda x: x)
    source = archive[0] / 'reference-bridge'
    source.mkdir()
    monkeypatch.setattr('api.services.tenders.gen_tech_plan._source_dir', lambda: source)
    original = b.model_dump()
    _, _, _, refs = store.generation_inputs(1, b, [], {})
    assert refs == [item['id'][:16]]
    assert b.model_dump() == original
    b.materialSelections['geology'].enabled = False
    assert store.generation_inputs(1, b, [], {})[3] == []
    assert not b.techPlanSourceIds


def test_scan_is_only_in_the_selected_existing_chapter_and_never_in_toc(archive):
    from api.services.tenders.document import _bookmark_paragraph, _write_toc_line
    from api.services.tenders.native_format import validate_native_toc
    from api.services.tenders.pdf_format import _attach_library

    item = upload()
    b = bid(item)
    slots, media, _, _ = store.generation_inputs(1, b, [], {})
    doc = Document()
    doc.add_paragraph('目录')
    row = doc.add_paragraph()
    _write_toc_line(row, 0, '项目管理机构', 'toc_people', '1')
    heading = doc.add_paragraph('项目管理机构', style='Heading 1')
    _bookmark_paragraph(heading, 'toc_people')
    doc.add_paragraph('人员材料正文。')
    other = doc.add_paragraph('其他资料', style='Heading 1')
    doc.add_paragraph('此处粘贴其他材料')
    _attach_library(doc, b, slots=slots, media=media)
    drawings = [p for p in doc.element.body if list(p.iter(qn('w:drawing')))]
    assert len(drawings) == 1
    elements = list(doc.element.body)
    assert elements.index(heading._p) < elements.index(drawings[0]) < elements.index(other._p)
    assert validate_native_toc(doc)['entries'] == 1


def test_new_scan_for_technical_section_does_not_enter_business_volume(archive):
    item = upload('product', '检测报告', '设备甲')
    b = bid(item)
    b.generateVolume = 'business'
    b.outlineItems = [OutlineItem(id='tech', title='技术方案', kind='tech_plan')]
    b.materialSelections['product'].targetSection = '技术方案'
    assert store.generation_inputs(1, b, [], {})[1] == {}


def test_numbered_toc_label_maps_to_unnumbered_original_body_anchor(archive, monkeypatch):
    item = upload()
    b = bid(item)
    b.materialSelections['personnel'].targetSection = '六、项目管理机构'
    monkeypatch.setattr(store, 'existing_sections', lambda _: ['六、项目管理机构', '项目管理机构'])
    slots, media, _, _ = store.generation_inputs(1, b, [], {})
    assert slots[0].attachToSection == '项目管理机构'
    assert slots[0].title != '项目管理机构'
    assert media[slots[0].key]


def test_unreadable_scan_is_archived_but_cannot_create_a_body_placeholder(archive):
    item = upload(data=b'not a real image')
    b = bid(item)
    before = b.model_dump()
    slots, media, notes, _ = store.generation_inputs(1, b, [], {})
    assert slots == [] and media == {} and notes
    assert b.model_dump() == before
    assert '无法读取' in ' '.join(store.preflight(1, b)['selected'][0]['issues'])


def test_business_only_reference_does_not_create_a_source_bridge(archive):
    item = upload('geology', '地勘', extension='.docx')
    b = bid(item, mode='reference')
    b.autoDraftTechPlan = True
    b.generateVolume = 'business'
    assert store.generation_inputs(1, b, [], {})[3] == []
    assert not (archive[0] / 'tech-plan-sources').exists()
    assert '只生成商务标' in ' '.join(store.preflight(1, b)['selected'][0]['issues'])


def test_archived_reference_unrelated_to_project_is_reported_and_not_adopted(archive):
    item = upload('geology', '地勘', extension='.docx')
    b = bid(item, mode='reference')
    b.projectName = '河南省胸科医院救护车采购'
    b.bidContent = '医疗救护车'
    b.autoDraftTechPlan = True
    assert store.generation_inputs(1, b, [], {})[3] == []
    assert '本项目不匹配' in ' '.join(store.preflight(1, b)['selected'][0]['issues'])


def test_authenticated_api_upload_list_download_and_delete(archive, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api.deps import get_current_user
    from api.routers.tender_materials import router
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1, is_superuser=True)
    with TestClient(app) as client:
        response = client.post('/materials', data={'category':'performance', 'subtype':'合同', 'entity':'项目甲'},
                               files={'file':('合同.txt', b'contract text', 'text/plain')})
        assert response.status_code == 200
        item = response.json()['data']
        assert not item['enabled']
        assert client.get('/materials').json()['data']['files'][0]['id'] == item['id']
        assert client.get('/materials/performance/' + item['id']).content == b'contract text'
        assert client.delete('/materials/performance/' + item['id']).status_code == 200
        assert client.get('/materials').json()['data']['files'] == []
