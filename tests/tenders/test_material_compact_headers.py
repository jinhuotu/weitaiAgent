from copy import deepcopy
from docx import Document
from docx.oxml.ns import qn

from api.services.tenders.material_layout import mark_repeating_headers
from api.services.tenders.placeholders import _draw_box, _slot_anchor
from api.services.tenders.schema import PlaceholderItem


def without_header_metadata(element):
    cloned = deepcopy(element)
    for flag in list(cloned.iter(qn('w:tblHeader'))):
        props = flag.getparent()
        props.remove(flag)
        if not len(props) and not props.attrib:
            props.getparent().remove(props)
    return cloned.xml


def test_real_column_headers_repeat_but_forms_and_blank_rows_do_not():
    doc = Document()
    table = doc.add_table(rows=20, cols=3)
    for cell, text in zip(table.rows[0].cells, ['编号', '名称', '数量']):
        cell.text = text
    form = doc.add_table(rows=3, cols=4)
    for cell, text in zip(form.rows[0].cells, ['姓名', '', '年龄', '']):
        cell.text = text
    empty = doc.add_table(rows=2, cols=2)
    before = [without_header_metadata(t._tbl) for t in doc.tables]
    assert mark_repeating_headers(doc) == 1
    assert table.rows[0]._tr.find('w:trPr/w:tblHeader',table._tbl.nsmap) is not None
    assert not list(form._tbl.iter(qn('w:tblHeader'))) and not list(empty._tbl.iter(qn('w:tblHeader')))
    assert [without_header_metadata(t._tbl) for t in doc.tables] == before
    assert mark_repeating_headers(doc) == 0


def test_header_style_can_identify_unseen_column_labels_without_word_list():
    doc = Document();table=doc.add_table(rows=3,cols=2)
    for cell, label in zip(table.rows[0].cells, ['陌生甲项', '陌生乙项']):
        cell.paragraphs[0].add_run(label).bold=True
    assert mark_repeating_headers(doc) == 1


def test_labelled_box_has_content_adaptive_height_and_no_blank_spacer_blocks():
    doc = Document()
    _draw_box(doc, PlaceholderItem(key='proof',title='专项证明'), wording='对应专项证明；所需材料：专项证明')
    assert len(doc.paragraphs) == 1
    p = doc.paragraphs[0]
    assert p.text == '对应专项证明；所需材料：专项证明'
    assert p.paragraph_format.keep_together
    assert not list(p._p.iter(qn('w:br')))
    assert p._p.find('w:pPr/w:pBdr', p._p.nsmap) is not None
    assert not any(br.get(qn('w:type')) == 'page' for br in doc.element.body.iter(qn('w:br')))


def test_direct_anchor_preserves_cross_paragraph_sentence_and_moves_below_table():
    doc = Document();first=doc.add_paragraph('附：汇款凭证')
    doc.add_paragraph('的复印件或扫描件。')
    doc.add_paragraph()
    table=doc.add_table(rows=2,cols=2)
    doc.add_paragraph('二、下一章',style='Heading 1')
    before=doc.element.xml
    assert _slot_anchor(first._p) is table._tbl
    assert doc.element.xml == before


def test_bond_variants_share_one_image_and_never_add_a_second_box(tmp_path):
    from PIL import Image
    from api.services.tenders.section_materials import append_section_materials
    doc=Document();doc.add_paragraph('投标保证金',style='Heading 1')
    doc.add_paragraph('附：投标保证金凭证复印件。')
    image=tmp_path/'receipt.png';Image.new('RGB',(80,50),'white').save(image)
    slots=[PlaceholderItem(key='bond',title='投标保证金缴存回单',attachToSection='投标保证金'),
           PlaceholderItem(key='material_new',title='投标保证金凭证复印件',attachToSection='投标保证金')]
    append_section_materials(doc,slots,{'bond':[image]},['bond','material_new'])
    marks=[mark.get(qn('w:name')) for mark in doc.element.body.iter(qn('w:bookmarkStart'))
           if mark.get(qn('w:name'),'').startswith('result_')]
    assert len(marks)==1 and marks[0].endswith('_img')


def test_alternative_identity_note_uses_its_form_role_instead_of_a_generic_box():
    from api.services.tenders.section_materials import _identity_family
    slot=PlaceholderItem(key='material_note',title='法定代表人或委托代理人身份证复印件')
    assert _identity_family(slot,'授权委托书')=='id_agent'
    assert _identity_family(slot,'法定代表人身份证明')=='id_legal'
