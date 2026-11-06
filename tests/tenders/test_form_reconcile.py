"""Manually labelled examples: only redundant form frames may disappear."""
import pytest
from docx import Document
from docx.oxml import OxmlElement

from api.services.cablepkg.form_reconcile import reconcile_form_placeholders, table_digest


def box(doc, title='技术偏差表'):
    p = doc.add_paragraph('对应 ' + title + '；所需材料：' + title)
    p._p.get_or_add_pPr().append(OxmlElement('w:pBdr'))
    return p._p


def deviation(doc, business=False, complete=True):
    table = doc.add_table(rows=1, cols=5)
    headers = (['序号', '招标文件条目号', '招标文件条款', '投标文件条款', '偏差说明'] if business else
               ['序号', '偏差事项', '招标文件要求', '投标文件响应', '偏差说明'])
    for cell, value in zip(table.rows[0].cells, headers):
        cell.text = value
    values = (['1', '', '交货时间：按合同约定交货', '按合同约定交货', '无偏差'] if business else
              ['1', '交货时间', '按合同约定交货', '按合同约定交货', '无偏差'])
    if not complete:
        values[3] = ''
    for cell, value in zip(table.add_row().cells, values):
        cell.text = value
    neutral = ['3', '', '其他：—', '', '无偏差'] if business else ['3', '其他', '—', '—', '无偏差']
    for cell, value in zip(table.add_row().cells, neutral):
        cell.text = value
    return table


@pytest.mark.parametrize('business,before', [(False, False), (True, True)])
def test_screenshot_layout_removes_only_frame_preserves_table_and_proof(business, before):
    doc = Document()
    title = '商务偏差表' if business else '技术偏差表'
    doc.add_heading('（一）' + title, 2)
    if before:
        frame = box(doc, title)
        doc.add_heading('1.' + title + '（格式）', 3)
    table = deviation(doc, business)
    original = table._tbl.xml
    if not before:
        frame = box(doc, title)
    proof = box(doc, '检测报告')
    doc.add_paragraph('原文签章要求：法定代表人签字。')
    removed = reconcile_form_placeholders(doc)
    assert [(r['kind'], r['title'], r['source']) for r in removed] == [('bizDeviation' if business else 'techDeviation', title, 'existing-format')]
    assert frame.getparent() is None
    assert proof.getparent() is not None
    assert table._tbl.xml == original
    assert doc.paragraphs[-1].text == '原文签章要求：法定代表人签字。'
    assert reconcile_form_placeholders(doc) == []


@pytest.mark.parametrize('value', ['', '请填写响应', '___', '〔待填〕'])
def test_unfilled_response_keeps_table_but_not_duplicate_material_frame(value):
    doc = Document()
    doc.add_heading('技术偏差表', 1)
    table = deviation(doc)
    table.cell(1, 3).text = value
    frame = box(doc)
    original = table._tbl.xml
    assert [r['source'] for r in reconcile_form_placeholders(doc)] == ['existing-format']
    assert frame.getparent() is None
    assert table._tbl.xml == original


def test_header_only_and_other_chapter_cannot_satisfy_missing_form():
    doc = Document()
    doc.add_heading('技术偏差表', 1)
    frame = box(doc)
    doc.add_heading('其他包件', 1)
    doc.add_heading('技术偏差表', 2)
    deviation(doc)
    assert reconcile_form_placeholders(doc) == []
    assert frame.getparent() is not None


def test_two_candidate_tables_are_ambiguous():
    doc = Document()
    doc.add_heading('技术偏差表', 1)
    deviation(doc)
    deviation(doc)
    frame = box(doc)
    assert reconcile_form_placeholders(doc) == []
    assert frame.getparent() is not None


def test_protected_directory_and_original_unframed_instructions_are_untouched():
    doc = Document()
    doc.add_heading('技术偏差表', 1)
    table = deviation(doc)
    frame = box(doc)
    instruction = doc.add_paragraph('对应 技术偏差表；所需材料：技术偏差表')
    before = doc.element.body.xml
    assert reconcile_form_placeholders(doc, excluded=[table._tbl]) == []
    assert doc.element.body.xml == before
    assert instruction._p.getparent() is not None
    assert frame.getparent() is not None


def test_existing_analysis_format_is_not_a_second_missing_attachment():
    doc = Document()
    doc.add_heading('货物清单单价分析表', 1)
    table = doc.add_table(rows=2, cols=9)
    for cell,value in zip(table.rows[0].cells, ['序号','组件材料项目名称','产地','制造商','单位','数量','单价（含税）','合价（含税）','备注']):
        cell.text = value
    frame = box(doc, '货物清单单价分析表')
    original = table._tbl.xml
    assert [r['source'] for r in reconcile_form_placeholders(doc)] == ['existing-format']
    assert frame.getparent() is None
    assert table._tbl.xml == original


def test_body_receipt_does_not_clean_another_scope():
    doc = Document()
    doc.add_heading('技术特性参数表', 1)
    frame = box(doc, '技术特性参数表')
    doc.add_heading('其他章节', 1)
    table = doc.add_table(rows=1, cols=1)
    table.cell(0, 0).text = '4.6'
    receipt = {'kind': 'technical', 'status': 'filled', 'tableDigests': [table_digest(table._tbl)]}
    assert reconcile_form_placeholders(doc, [receipt]) == []
    assert frame.getparent() is not None


@pytest.mark.parametrize('complete,imported', [(True, False), (False, False), (False, True)])
def test_generate_final_document_cleans_after_actual_import(tmp_path, monkeypatch, complete, imported):
    from api.services.tenders import generate, assemble, native_format, material_intents
    from api.services.tenders import invitation_attachment, company_identity
    from api.services.tenders.schema import BidBrief
    from api.services import bid_table_import as service

    monkeypatch.setattr(generate, 'tenders_output_dir', lambda: tmp_path)
    monkeypatch.setattr(native_format, 'prefer_native_format', lambda *_: False)
    monkeypatch.setattr(material_intents, 'prepare_structural_demands', lambda *_: None)
    monkeypatch.setattr(material_intents, 'coverage', lambda *_: {})
    monkeypatch.setattr(invitation_attachment, 'insert_required_invitation', lambda *_: [])
    monkeypatch.setattr(company_identity, 'missing_company_notes', lambda *_: [])
    data = {'confirmed': True, 'invitationId': 'project', 'testData': True,
            'rows': [{'kind': 'bizDeviation', 'code': '', 'key': 'bizDeviation|交货时间',
                      'values': ['1', '交货时间', '合同约定时间', '按合同约定交货', '无偏差']}]}
    monkeypatch.setattr(service, 'load_import', lambda *_: data)
    source_tables = []

    def builder(brief, dest, **kwargs):
        doc = Document()
        doc.add_heading('（一）商务偏差表', 2)
        box(doc, '商务偏差表')
        doc.add_heading('1.商务偏差表（格式）', 3)
        table = deviation(doc, business=True, complete=complete)
        source_tables.append(table._tbl.xml)
        doc.add_heading('所投产品检测报告', 2)
        box(doc, '检测报告')
        doc.save(dest)
        return dest, ['原有生成提示']

    monkeypatch.setattr(assemble, 'assemble_bid_docx', builder)
    brief = BidBrief(attachQualifications=False, invitationId='project',
                     bidTableImportId='a' * 32 if imported else '')
    result = generate.generate_bid(brief)
    actual = Document(tmp_path / result['docxFile'])
    frames = [p.text for p in actual.paragraphs if p._p.find('w:pPr/w:pBdr', p._p.nsmap) is not None]
    assert ('对应 商务偏差表；所需材料：商务偏差表' in frames) is False
    assert '对应 检测报告；所需材料：检测报告' in frames
    assert '原有生成提示' in result['warnings']
    assert len(result['formPlaceholderCleanup']['business']) == 1
    if imported:
        assert actual.tables[0].cell(1, 3).text == '按合同约定交货'
        assert actual.tables[0].cell(1, 2).text == '交货时间：合同约定时间'
        assert any('不构成可投标证据' in warning for warning in result['warnings'])
    else:
        assert actual.tables[0]._tbl.xml == source_tables[0]


def test_pair_merge_retains_cleanup_per_volume():
    from api.services.tenders.generate import merge_generated
    result = merge_generated({'formPlaceholderCleanup': {'business': ['b']}},
                             {'formPlaceholderCleanup': {'technical': ['t']}})
    assert result['formPlaceholderCleanup'] == {'business': ['b'], 'technical': ['t']}


def test_format_presence_does_not_claim_failed_import_was_successful():
    doc = Document()
    doc.add_heading('技术偏差表', 1)
    deviation(doc)
    frame = box(doc)
    result = reconcile_form_placeholders(doc, [{'kind': 'techDeviation', 'status': 'not_written', 'tableDigests': []}])
    assert [row['source'] for row in result] == ['existing-format']
    assert frame.getparent() is None


def test_native_heading_stops_backwards_scan_before_previous_unlabelled_table():
    doc=Document()
    doc.add_table(rows=2,cols=10).cell(0,0).text='前一节报价表'
    doc.add_paragraph('货物清单单价分析表')
    table=doc.add_table(rows=2,cols=9)
    for cell,value in zip(table.rows[0].cells,['序号','组件材料项目名称','产地','制造商','单位','数量','单价（含税）','合价（含税）','备注']):cell.text=value
    frame=box(doc,'货物清单单价分析表')
    doc.add_paragraph('商务文件')
    deviation(doc,business=True)
    assert [r['title'] for r in reconcile_form_placeholders(doc)]==['货物清单单价分析表']
    assert frame.getparent() is None


def test_user_generated_document_keeps_original_tables_and_removes_same_form_frames():
    from pathlib import Path
    import pytest
    from api.services.tenders.directory_guard import directory_blocks
    source=Path('E:/downLoad/国家电网有限公司2026年华中、川渝区域第一次联合采购低压电力电缆、10kV电力-投标文件 (2).docx')
    if not source.exists():pytest.skip('用户实测标书仅在本机提供')
    doc=Document(source)
    original=[table._tbl.xml for table in doc.tables]
    removed=reconcile_form_placeholders(doc,excluded=directory_blocks(doc))
    titles={row['title'] for row in removed}
    assert {'货物清单单价分析表','商务偏差表','技术偏差表'}<=titles
    assert [table._tbl.xml for table in doc.tables]==original
    assert any('所需材料：资格预审合格通知书' in p.text for p in doc.paragraphs)
