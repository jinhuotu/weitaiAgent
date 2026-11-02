"""An explicitly identified back remains a back even with cached ID metadata."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from docx import Document
from docx.oxml.ns import qn
from PIL import Image

from api.services.tenders import library_kb
from api.services.tenders.id_card import assign_person, dump_id_meta, age_from_id, gender_from_id
from api.services.tenders.schema import PlaceholderItem
from api.services.tenders.section_materials import append_section_materials


ID_NO = '11010119900101001X'


@pytest.mark.parametrize('with_id', [False, True])
def test_back_side_wins_over_cached_id_number(with_id):
    front = assign_person({'side': 'front', 'idNo': ID_NO, 'name': '测试人员'}, [])
    back = {'side': 'back', 'idNo': ID_NO if with_id else ''}
    before = deepcopy(back)
    assigned = assign_person(back, [front])
    assert front['side'] == 'front' and assigned['side'] == 'back'
    assert assigned['personKey'] == front['personKey'] == ID_NO
    assert assigned['idNo'] == ID_NO
    assert assigned['name'] == front['name']
    assert back == before


@pytest.mark.parametrize('side', ['front', 'unknown', '', None])
def test_non_back_id_number_keeps_exact_front_completion_behavior(side):
    card = {'side': side, 'idNo': ID_NO, 'name': '测试人员'}
    assert assign_person(card, []) == dict(card, side='front', personKey=ID_NO,
                                         gender=gender_from_id(ID_NO), age=age_from_id(ID_NO))


def test_unpaired_back_with_id_is_not_reclassified():
    card = {'side': 'back', 'idNo': ID_NO}
    assert assign_person(card, []) == card


@pytest.mark.asyncio
@pytest.mark.parametrize('back_first', [False, True])
async def test_library_groups_filters_and_renders_both_faces_with_cached_back_id(tmp_path, monkeypatch, back_first):
    paths = {}
    for side, color in [('front', 'blue'), ('back', 'red')]:
        path = tmp_path / (side + '.png')
        Image.new('RGB', (856, 540), color).save(path)
        paths[side] = path
    # Replaying this persisted back used to turn it into a second front.
    cards = {'front': {'side': 'front', 'idNo': ID_NO, 'personKey': ID_NO, 'name': '测试人员'},
             'back': {'side': 'back', 'idNo': ID_NO, 'personKey': ID_NO, 'name': '测试人员'}}
    order = ['back', 'front'] if back_first else ['front', 'back']
    originals = [(SimpleNamespace(id=i, public_id=side, summary=dump_id_meta(cards[side])), paths[side])
                 for i, side in enumerate(order, 1)]
    monkeypatch.setattr(library_kb, '_get_parent', AsyncMock(return_value=(object(), [])))
    monkeypatch.setattr(library_kb, '_iter_file_docs', lambda *args: list(originals))
    people = await library_kb.legal_persons_from_library(None)
    assert len(people) == 1 and people[0]['key'] == ID_NO
    assert set(people[0]['sides']) == {'front', 'back'}
    selected = await library_kb.filter_legal_id_attachments(None, [path for _, path in originals], ID_NO)
    assert selected == [paths['front'], paths['back']]

    doc = Document()
    doc.add_paragraph('法定代表人身份证明', style='Heading 1')
    doc.add_paragraph('附：法定代表人身份证复印件或扫描件。')
    doc.add_paragraph('下一原版章节', style='Heading 1')
    source = [(node, node.xml) for node in doc.element.body]
    slot = PlaceholderItem(key='id_legal', title='法定代表人身份证', attachToSection='法定代表人身份证明')
    append_section_materials(doc, [slot], {'id_legal': selected}, ['id_legal'])
    outcomes = [mark for mark in doc.element.body.iter(qn('w:bookmarkStart'))
                if mark.get(qn('w:name'), '').startswith('result_')]
    assert len(outcomes) == 1 and outcomes[0].get(qn('w:name')).endswith('_img')
    assert len(list(outcomes[0].getparent().iter(qn('w:drawing')))) == 2
    assert all(node.xml == xml for node, xml in source)
