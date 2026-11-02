from docx import Document
from docx.oxml.ns import qn

from api.services.tenders.schema import PlaceholderItem
from api.services.tenders.section_materials import append_section_materials


def text(el):
    return ''.join(t.text or '' for t in el.iter(qn('w:t')))


def test_append_order_preserves_source_and_is_idempotent():
    for material in ['设备检测证明', '服务资格材料', '陌生产品认证']:
        doc = Document()
        doc.add_paragraph('资料要求', style='Heading 1')
        for letter in 'ABCDE':
            doc.add_paragraph(f'{letter}. 须提供{material}{letter}')
        remark = doc.add_paragraph('备注：全部材料按要求提交。')
        stop = doc.add_paragraph('下一章', style='Heading 1')
        source = [(el, el.xml) for el in doc.element.body]
        slots = [PlaceholderItem(key=c, title=material+c, attachToSection='资料要求') for c in reversed('ABCDE')]
        notes, handled = append_section_materials(doc, slots, {}, list('EDCBA'))
        assert handled == set('ABCDE')
        for el, xml in source:
            assert el.xml == xml
        added = []
        cur = remark._p.getnext()
        while cur is not stop._p:
            added.append(text(cur))
            cur = cur.getnext()
        blob = ''.join(added)
        assert all(f'对应 {c}.' in blob for c in 'ABCDE')
        assert [blob.index(f'对应 {c}.') for c in 'ABCDE'] == sorted(blob.index(f'对应 {c}.') for c in 'ABCDE')
        before = doc.element.body.xml
        append_section_materials(doc, slots, {}, list('ABCDE'))
        assert doc.element.body.xml == before


def test_letter_exemption_and_toc_exclusion():
    doc = Document()
    toc = doc.add_paragraph('资料要求', style='Heading 1')
    toc_item = doc.add_paragraph('A. 须提供证明材料')
    doc.add_paragraph('合同条款响应书', style='Heading 1')
    doc.add_paragraph('A. 本单位承诺提供证明材料。')
    doc.add_paragraph('单位：________ 签字盖章：________')
    before = doc.element.body.xml
    append_section_materials(doc, [PlaceholderItem(key='a', title='证明材料')], {}, ['a'], [toc._p, toc_item._p])
    assert doc.element.body.xml == before


def test_form_only_fills_existing_blank_spans():
    from api.services.tenders.schema import default_brief
    from api.services.tenders.section_materials import fill_form_gaps
    doc = Document()
    doc.add_paragraph('合同条款响应书', style='Heading 1')
    para = doc.add_paragraph('补充说明：________。')
    signature = doc.add_paragraph('签字盖章：________')
    before = list(doc.element.body)
    attributes = para._p.pPr.xml if para._p.pPr is not None else None
    fill_form_gaps(doc, default_brief())
    assert list(doc.element.body) == before
    assert para.text == '补充说明：无。'
    assert signature.text == '签字盖章：________'
    assert (para._p.pPr.xml if para._p.pPr is not None else None) == attributes


def test_all_lettered_items_survive_heading_styles_and_missing_catalog():
    doc = Document()
    doc.add_paragraph('一、资格资信证明文件', style='Heading 1')
    for letter, name in zip('ABCDE', ['公司简介', '登记证明', '类似工作证明', '财务证明', '其他凭证']):
        doc.add_paragraph(f'{letter}.{name}', style='Heading 1')
    remark = doc.add_paragraph('备注：以上材料均需提交。')
    stop = doc.add_paragraph('二、分项报价表', style='Heading 1')
    original = [(el, el.xml) for el in doc.element.body]
    slots = [PlaceholderItem(key='b', title='登记证明')]
    append_section_materials(doc, slots, {}, ['b'])
    blob = ''
    node = remark._p.getnext()
    while node is not stop._p:
        blob += text(node)
        node = node.getnext()
    for letter in 'ABCDE':
        assert blob.count(f'对应 {letter}.') == 1
    for node, xml in original:
        assert node.xml == xml
    before = doc.element.body.xml
    append_section_materials(doc, slots, {}, ['b'])
    assert doc.element.body.xml == before


def test_saved_orphan_items_already_in_parent_do_not_render_twice():
    from api.services.tenders.outline import retain_lettered_children
    from api.services.tenders.schema import OutlineItem
    parent = OutlineItem(id='p', title='资料要求', level=1, body='A.组织介绍\nB.登记证明\nC.业绩要求\nD.财务要求\nE.其他材料')
    children = [OutlineItem(id=c, title=title, level=1) for c, title in [('c','C.业绩要求'), ('d','财务要求'), ('e','E.其他材料')]]
    assert retain_lettered_children([parent, *children]) == [parent]


def test_real_slot_files_are_used_when_media_map_is_empty(tmp_path, monkeypatch):
    from PIL import Image
    from api.services.tenders import slots as store
    monkeypatch.setattr(store, 'slots_root', lambda: tmp_path)
    folder = tmp_path / 'proof'
    folder.mkdir()
    Image.new('RGB', (24, 24), 'white').save(folder / 'proof.png')
    doc = Document()
    doc.add_paragraph('资料要求', style='Heading 1')
    doc.add_paragraph('A.检测证明')
    slot = PlaceholderItem(key='proof', title='检测证明')
    append_section_materials(doc, [slot], {}, ['proof'])
    assert list(doc.element.body.iter(qn('w:drawing')))


def test_child_section_names_and_cross_stage_deduplication():
    doc = Document()
    doc.add_paragraph('一、资格、资信证明文件', style='Heading 1')
    doc.add_paragraph('C.业绩要求：需提供证明材料')
    doc.add_paragraph('D.财务要求：需提供财务材料')
    slots = [PlaceholderItem(key='c', title='业绩要求', attachToSection='C.业绩要求'),
             PlaceholderItem(key='d', title='财务要求', attachToSection='D.财务要求')]
    _, handled = append_section_materials(doc, slots, {}, ['c', 'd'])
    assert handled == {'c', 'd'}
    before = doc.element.body.xml
    changed = [slot.model_copy(update={'attachToSection': '一、资格、资信证明文件'}) for slot in slots]
    append_section_materials(doc, changed, {}, ['c', 'd'])
    assert doc.element.body.xml == before


def test_orphans_after_pricing_are_removed_from_render_outline():
    from api.services.tenders.outline import retain_lettered_children
    from api.services.tenders.schema import OutlineItem
    parent = OutlineItem(id='p', title='资料要求', body='C.业绩要求\nD.财务要求\nE.其他材料')
    quote = OutlineItem(id='q', title='分项报价表', kind='quote')
    children = [OutlineItem(id=letter, title=f'{letter}.{title}') for letter, title in [('C', '业绩要求'), ('D', '财务要求'), ('E', '其他材料')]]
    assert retain_lettered_children([parent, quote, *children]) == [parent, quote]


def test_final_validation_rejects_duplicate_orphan_modules():
    import pytest
    from common.errors import AppError
    from api.services.tenders.section_materials import validate_material_uniqueness
    doc = Document()
    doc.add_paragraph('材料', style='Heading 1')
    doc.add_paragraph('C.工作证明', style='Heading 1')
    doc.add_paragraph('报价', style='Heading 1')
    doc.add_paragraph('C.工作证明', style='Heading 1')
    with pytest.raises(AppError):
        validate_material_uniqueness(doc)


def test_explicit_empty_scoped_media_never_borrows_other_company_files(tmp_path, monkeypatch):
    from api.services.tenders import slots as store
    monkeypatch.setattr(store, 'list_slot_files', lambda key: (_ for _ in ()).throw(AssertionError('bypassed scoped media')))
    doc = Document()
    doc.add_paragraph('材料', style='Heading 1')
    doc.add_paragraph('A.资格证明')
    append_section_materials(doc, [PlaceholderItem(key='proof', title='资格证明')], {'proof': []}, ['proof'])
    assert not list(doc.element.body.iter(qn('w:drawing')))


def test_word_upperletter_ilvl_zero_stays_under_parent():
    from docx.oxml import OxmlElement
    doc = Document()
    root = doc.part.numbering_part.element
    abstract = OxmlElement('w:abstractNum')
    abstract.set(qn('w:abstractNumId'), '71')
    level = OxmlElement('w:lvl'); level.set(qn('w:ilvl'), '0')
    for name, value in [('start','1'), ('numFmt','upperLetter'), ('lvlText','%1.')]:
        node = OxmlElement('w:' + name); node.set(qn('w:val'), value); level.append(node)
    abstract.append(level); root.append(abstract)
    number = OxmlElement('w:num'); number.set(qn('w:numId'), '71')
    link = OxmlElement('w:abstractNumId'); link.set(qn('w:val'), '71'); number.append(link); root.append(number)
    doc.add_paragraph('证明材料', style='Heading 1')
    for name in ['组织介绍', '登记文件', '工作记录', '财务记录', '其他证明']:
        para = doc.add_paragraph(name, style='Heading 1')
        props = OxmlElement('w:numPr')
        for tag, value in [('ilvl', '0'), ('numId', '71')]:
            node = OxmlElement('w:' + tag); node.set(qn('w:val'), value); props.append(node)
        para._p.get_or_add_pPr().append(props)
    remark = doc.add_paragraph('备注：按上述要求提交。')
    stop = doc.add_paragraph('报价', style='Heading 1')
    append_section_materials(doc, [], {}, [])
    body = ''
    cursor = remark._p.getnext()
    while cursor is not stop._p:
        body += text(cursor); cursor = cursor.getnext()
    assert all(body.count(f'对应 {letter}.') == 1 for letter in 'ABCDE')


def test_library_import_uses_slot_path_and_keeps_original_bytes(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from api.services.tenders import library_kb
    monkeypatch.setattr(library_kb, 'resolve_storage_path', lambda key: tmp_path / key)
    source = tmp_path / 'original.png'
    source.write_bytes(b'original-scan-bytes')
    parent = SimpleNamespace(public_id='parent', tags=['slot:proof'])
    child = library_kb._copy_file_as_child(SimpleNamespace(public_id='base', id=1), parent, source)
    assert child.file_key.startswith('tender-assets/slots/proof/')
    assert (tmp_path / child.file_key).read_bytes() == source.read_bytes()
    assert library_kb._doc_file_path(child) is not None
    (tmp_path / child.file_key).write_bytes(b'')
    assert library_kb._doc_file_path(child) is None


def test_short_duplicate_modules_removed_without_touching_full_source():
    from api.services.tenders.section_materials import remove_repeated_child_modules
    doc = Document()
    doc.add_paragraph('资料要求', style='Heading 1')
    source = [doc.add_paragraph(f'{letter}.材料要求：需要提交完整证明。') for letter in 'CDE']
    snapshots = [p._p.xml for p in source]
    doc.add_paragraph('分项报价表', style='Heading 1')
    for letter in 'CDE':
        doc.add_paragraph(f'{letter}.材料要求', style='Heading 1')
        doc.add_paragraph('重复模块正文')
    assert remove_repeated_child_modules(doc) == 3
    assert [p._p.xml for p in source] == snapshots
    assert '重复模块正文' not in '\n'.join(p.text for p in doc.paragraphs)
