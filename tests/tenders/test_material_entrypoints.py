import ast
import hashlib
import pytest
from pathlib import Path
from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from api.services.tenders.placeholders import _drop_after,place_unmatched_slots
from api.services.tenders.schema import PlaceholderItem
from api.services.tenders.classifier import material_output_coverage


def test_private_writers_have_only_one_caller():
    root=Path(__file__).parents[2]/'apps/api/services/tenders'
    calls=[]
    for filename in ['placeholders.py','section_materials.py','pdf_format.py','scan_placement.py']:
        tree=ast.parse((root/filename).read_text(encoding='utf-8'))
        for function in [node for node in ast.walk(tree) if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef))]:
            for node in ast.walk(function):
                if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id in {'_insert_slot_media','_draw_box'}:
                    calls.append((filename,function.name,node.func.id))
    assert {name for _,name,_ in calls}=={'_drop_after'}


def test_run_anchor_is_promoted_to_complete_body_paragraph():
    doc=Document();p=doc.add_paragraph('保证金原版首句。')
    run=p.add_run('原版后半句不能拆开。')
    stop=doc.add_paragraph('下一章')
    text=p.text
    _drop_after(doc,run._r,PlaceholderItem(key='proof',title='原始凭证'),[],at_marker=True)
    assert p.text==text
    assert p._p.getnext() is not stop._p
    assert not list(p._p.iter(qn('w:pBdr')))
    assert not list(p._p.iter(qn('w:drawing')))


def test_multiple_requirements_in_one_source_paragraph_are_not_dropped():
    doc=Document();doc.add_paragraph('材料清单',style='Heading 1')
    doc.add_paragraph('附：营业执照复印件、陌生许可证扫描件、任职证明扫描件。')
    place_unmatched_slots(doc,slots=[],media={},required_keys=[])
    names=[b.get(qn('w:name'),'') for b in doc.element.body.iter(qn('w:bookmarkStart'))]
    assert len([name for name in names if name.startswith('result_')])==3


def test_structured_requirement_is_not_lost_when_catalog_is_empty():
    from api.services.tenders.schema import BidRequirement
    doc=Document();doc.add_paragraph('核验材料',style='Heading 1')
    doc.add_paragraph('以下为完整原版说明。')
    requirement=BidRequirement(slotKey='verification',targetTitle='核验凭证',
                               attachToSection='核验材料',volume='business')
    place_unmatched_slots(doc,slots=[],media={},required_keys=[],
                          requirements=[requirement],volume='business')
    report=material_output_coverage(doc,{'verification':'核验材料'})
    assert report['outputs']==1 and report['unmet']==0


@pytest.mark.parametrize('has_file', [False, True])
def test_bond_chapter_keeps_every_explicit_requirement(tmp_path, has_file):
    from PIL import Image
    doc=Document();doc.add_paragraph('投标保证金',style='Heading 1')
    source=doc.add_paragraph('保证金证明材料按规定附后，正文不得删改。')
    slots=[PlaceholderItem(key='bond',title='投标保证金凭证',attachToSection='投标保证金'),
           PlaceholderItem(key='account_proof',title='基本账户证明',attachToSection='投标保证金')]
    media={}
    if has_file:
        scan=tmp_path/'account.png'
        Image.new('RGB',(120,80),'white').save(scan)
        media['account_proof']=[scan]
    place_unmatched_slots(doc,slots=slots,media=media,required_keys=[slot.key for slot in slots])
    report=material_output_coverage(doc,{slot.key:'投标保证金' for slot in slots})
    assert report['expected']==report['outputs']==2
    assert report['unmet']==report['duplicates']==0
    assert len(list(doc.element.body.iter(qn('w:drawing'))))==int(has_file)
    assert source.text=='保证金证明材料按规定附后，正文不得删改。'


def test_existing_empty_frame_is_replaced_as_a_whole_block():
    doc=Document();doc.add_paragraph('证明材料',style='Heading 1')
    empty=doc.add_paragraph()
    empty._p.get_or_add_pPr().append(OxmlElement('w:pBdr'))
    slot=PlaceholderItem(key='proof',title='通用认证',attachToSection='证明材料')
    place_unmatched_slots(doc,slots=[slot],media={'proof':[]},required_keys=['proof'])
    borders=[p for p in doc.element.body.iter(qn('w:p')) if p.find('w:pPr/w:pBdr',p.nsmap) is not None]
    assert len(borders)==1
    assert '通用认证' in ''.join(t.text or '' for t in borders[0].iter(qn('w:t')))


def test_legacy_marker_does_not_suppress_license_result():
    from api.services.tenders.section_materials import _token
    doc=Document();doc.add_paragraph('资格审查资料',style='Heading 1')
    p=doc.add_paragraph('附：营业执照扫描件。')
    mark=OxmlElement('w:bookmarkStart');mark.set(qn('w:id'),'42')
    mark.set(qn('w:name'),_token('renderedkey_','license'));p._p.append(mark)
    slot=PlaceholderItem(key='license',title='营业执照',attachToSection='资格审查资料')
    for _ in range(2):
        place_unmatched_slots(doc,slots=[slot],media={},required_keys=['license'])
    report=material_output_coverage(doc,{'license':'资格审查资料'})
    assert report['outputs']==1 and report['duplicates']==report['unmet']==0


def test_coverage_checks_physical_chapter_not_just_bookmark_existence():
    doc=Document();doc.add_paragraph('第一材料章',style='Heading 1')
    anchor=doc.add_paragraph('完整说明')
    other=doc.add_paragraph('第二材料章',style='Heading 1')
    _drop_after(doc,anchor._p,PlaceholderItem(key='proof',title='通用认证'),[],at_marker=True)
    name='result_'+hashlib.sha256(b'proof').hexdigest()[:20]+'_box'
    mark=next(b for b in doc.element.body.iter(qn('w:bookmarkStart')) if b.get(qn('w:name'))==name)
    other._p.addnext(mark.getparent())
    report=material_output_coverage(doc,{'proof':'第一材料章'})
    assert report['unmet']==report['wrong_chapter']==1
