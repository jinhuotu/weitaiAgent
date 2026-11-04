"""Regression examples for detached/shared material insertion boundaries."""
import hashlib
from collections import Counter
from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
import pytest
from api.services.tenders.schema import PlaceholderItem
from api.services.tenders import section_materials as materials


def slot(key, chapter='不存在的章节'):
    return PlaceholderItem(key=key, title='专项资质证明'+key, attachToSection=chapter)


def counts(doc):
    return Counter(mark.get(qn('w:name')) for mark in doc.element.body.iter(qn('w:bookmarkStart')) if mark.get(qn('w:name'), '').startswith('result_'))


def assert_results(doc, slots, suffix='_box'):
    actual=counts(doc)
    assert actual == Counter({'result_'+hashlib.sha256(s.key.encode()).hexdigest()[:20]+suffix:1 for s in slots})


def test_three_synthetic_materials_have_unique_live_boundaries(monkeypatch):
    doc=Document(); doc.add_paragraph('采购说明。')
    slots=[slot(key) for key in ('alpha','beta','gamma')]
    original=materials.insert_material; anchors=[]
    def capture(doc, anchor, *args, **kwargs):
        assert anchor.getparent() is doc.element.body
        anchors.append(anchor)
        return original(doc,anchor,*args,**kwargs)
    monkeypatch.setattr(materials,'insert_material',capture)
    notes,handled=materials.append_section_materials(doc,slots,{},[s.key for s in slots])
    assert handled=={s.key for s in slots}
    assert len(set(anchors))==3
    assert_results(doc,slots)
    assert not any('已跳过' in n for n in notes)
    before=doc.element.body.xml
    materials.append_section_materials(doc,slots,{},[s.key for s in slots])
    assert doc.element.body.xml==before
    assert all('anchor' not in decision and 'source' not in decision and 'head' not in decision for decision in doc._element._material_plans.values())


def test_two_materials_share_empty_template_frame(caplog):
    doc=Document();doc.add_paragraph('专项证明材料',style='Heading 1')
    frame=doc.add_paragraph()._p
    borders=OxmlElement('w:pBdr'); top=OxmlElement('w:top');top.set(qn('w:val'),'dashed');borders.append(top);frame.get_or_add_pPr().append(borders)
    slots=[slot('alpha','专项证明材料'),slot('beta','专项证明材料')]
    with caplog.at_level('INFO',logger=materials.__name__):
        notes,handled=materials.append_section_materials(doc,slots,{},['alpha','beta'])
    assert frame.getparent() is None
    assert handled=={'alpha','beta'}
    assert_results(doc,slots)
    assert 'relocated=True' in caplog.text
    assert not any('已跳过' in n for n in notes)


def test_deleted_anchor_relocates_to_owner_heading():
    doc=Document();head=doc.add_paragraph('指定章节',style='Heading 1')._p
    stale=doc.add_paragraph('原始来源')._p;doc.element.body.remove(stale)
    item=slot('alpha','指定章节')
    plan={'slot':item,'chapter':'指定章节','head':head,'anchor':stale,'source':stale,'fallback':False}
    anchor=materials.resolve_material_anchor(doc,plan)
    assert anchor is head
    materials.insert_material(doc,anchor,item,[],wording='所需材料：'+item.title)
    assert_results(doc,[item])


def test_failed_relocation_and_fallback_are_visible_notes(monkeypatch):
    doc=Document();doc.add_paragraph('指定章节',style='Heading 1')
    def failed(*a,**kw): raise RuntimeError('no writable boundary')
    monkeypatch.setattr(materials,'resolve_material_anchor',failed)
    notes,handled=materials.append_section_materials(doc,[slot('alpha','指定章节')],{},['alpha'])
    assert handled==set()
    assert any('占位渲染失败，已跳过' in note for note in notes)


def test_real_image_survives_cache_reentry(tmp_path):
    from PIL import Image
    image=tmp_path/'proof.png';Image.new('RGB',(32,32),'blue').save(image)
    doc=Document();doc.add_paragraph('专项证明材料',style='Heading 1')
    item=slot('alpha','专项证明材料')
    for _ in range(2):
        materials.append_section_materials(doc,[item],{'alpha':[image]},['alpha'])
        assert len(list(doc.element.body.iter(qn('w:drawing'))))==1
        assert_results(doc,[item],'_img')


@pytest.mark.parametrize('run',range(3))
def test_138_missing_materials_complete_reproducibly(run,tmp_path):
    doc=Document();doc.add_paragraph('采购说明。')
    slots=[slot(f'proof_{i:03}') for i in range(138)]
    notes,handled=materials.append_section_materials(doc,slots,{},[s.key for s in slots])
    assert len(handled)==138
    assert_results(doc,slots)
    assert any('138 项，输出 138 项，未满足 0' in n for n in notes)
    assert not any('已跳过' in n for n in notes)
    doc.save(tmp_path/f'missing-138-{run}.docx')

def test_cached_box_upgrades_to_image_once_after_shared_anchor_removed(tmp_path):
    from PIL import Image
    image=tmp_path/'proof.png';Image.new('RGB',(32,32),'blue').save(image)
    doc=Document();doc.add_paragraph('专项证明材料',style='Heading 1')
    anchor=doc.add_paragraph()._p
    borders=OxmlElement('w:pBdr');anchor.get_or_add_pPr().append(borders)
    item=slot('alpha','专项证明材料')
    materials.append_section_materials(doc,[item],{},['alpha'])
    assert anchor.getparent() is None
    notes,handled=materials.append_section_materials(doc,[item],{'alpha':[image]},['alpha'])
    assert handled=={'alpha'}
    assert_results(doc,[item],'_img')
    assert len(list(doc.element.body.iter(qn('w:drawing'))))==1
    assert not any('已跳过' in n for n in notes)


def test_failed_material_does_not_stop_later_materials(monkeypatch):
    doc=Document();doc.add_paragraph('指定章节',style='Heading 1')
    original=materials.resolve_material_anchor
    def resolve(doc,plan):
        if plan['slot'].key=='beta': raise RuntimeError('boundary unavailable')
        return original(doc,plan)
    monkeypatch.setattr(materials,'resolve_material_anchor',resolve)
    notes,handled=materials.append_section_materials(doc,[slot('alpha','指定章节'),slot('beta','指定章节')],{},['alpha','beta'])
    assert handled=={'alpha'}
    assert_results(doc,[slot('alpha')])
    assert any('beta' in n and '已跳过' in n for n in notes)
