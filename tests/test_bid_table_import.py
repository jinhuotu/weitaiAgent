from collections import Counter
from copy import deepcopy
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from openpyxl import Workbook
import pytest

from api.services import bid_table_import as service

ID = 'abc123def456'
A = '9985-500108478-00001'
B = '9985-500108449-00001'


def row(kind, code, values, suffix='1'):
    return {'key': f'{kind}|{code}|{suffix}', 'kind': kind, 'code': code, 'values': values, 'source': '人工标注独立用例'}


def quote(code=A):
    return row('quote', code, ['河南公司', '项目甲', '铜芯电缆', '千米', '2', '100', '200', '13', '113', '226'])


@pytest.fixture
def stored(tmp_path, monkeypatch):
    monkeypatch.setattr(service, 'store_root', lambda: tmp_path)
    return tmp_path


def test_decimal_and_empty_guarantee_checks():
    assert service.validate([quote()]) == []
    bad = quote()
    bad['values'][9] = '227'
    assert '报价金额不一致' in service.validate([bad])[0]
    tech = row('technical', A, ['1.1', '※绝缘厚度', 'mm', '≥4.5', '满足要求'])
    assert '保证值' in service.validate([tech])[0]


def test_differences_are_field_specific_numeric_format_is_equivalent():
    a = row('technical', A, ['1.1', '绝缘厚度', 'mm', '≥4.5', '4.5'])
    b = deepcopy(a)
    b['values'][4] = '4.500'
    source = lambda r: {'rows': [r], 'warnings': [], 'testData': False}
    assert service.reconcile([source(a), source(b)])['conflicts'] == []
    b['values'][4] = '4.1'
    conflict = service.reconcile([source(a), source(b)])['conflicts'][0]
    assert conflict['fields'] == ['投标人保证值']
    assert conflict['left']['values'][4] == '4.5'
    assert conflict['right']['values'][4] == '4.1'


def test_confirmation_requires_choices_owner_context_and_is_immutable(stored):
    a = row('technical', A, ['1', '电压', 'kV', '10', '10'])
    b = deepcopy(a)
    b['values'][4] = '12'
    source = lambda r: {'rows': [r], 'warnings': [], 'testData': False}
    preview = service.save_preview(7, ID, service.reconcile([source(a), source(b)]))
    with pytest.raises(ValueError, match='逐项'):
        service.confirm(preview['id'], 7, ID, {})
    with pytest.raises(ValueError, match='当前用户'):
        service.confirm(preview['id'], 8, ID, {})
    with pytest.raises(ValueError, match='当前用户'):
        service.confirm(preview['id'], 7, 'other', {})
    snapshot = service.confirm(preview['id'], 7, ID, {a['key']: 'right'})
    assert snapshot['id'] != preview['id']
    assert snapshot['rows'][0]['values'][4] == '12'
    assert service.load_import(preview['id'])['confirmed'] is False


def test_excel_selects_families_instead_of_largest_sheet(tmp_path):
    wb = Workbook()
    ws = wb.active
    ws.title = '货物报价'
    ws.append(['项目单位', '项目名称', '物资描述', '单位', '数量', '未含税单价（元）', '未含税合价（元）', '税率（%）', '含税单价（元）', '含税合价（元）', '技术规范ID'])
    ws.append([*quote()['values'], A])
    ws = wb.create_sheet('单价分析')
    ws.append(['技术规范ID', '序号', '组件材料项目名称', '单位', '数量', '单价（含税，元）', '合价（含税，元）'])
    for i in range(3):
        ws.append([A, str(i + 1), f'材料{i}', '千克', '1', '10', '10'])
    path = tmp_path / 'input.xlsx'
    wb.save(path)
    parsed = service.parse_file(path, path.name)
    assert Counter(r['kind'] for r in parsed['rows']) == {'quote': 1, 'analysis': 3}
    q = next(r for r in parsed['rows'] if r['kind'] == 'quote')
    assert q['values'][2:5] == ['铜芯电缆', '千米', '2']
    assert q['values'][9] == '226'


def test_fill_clones_one_table_per_id_preserves_header_and_notes(stored):
    data = [row('technical', A, ['1.1', '绝缘厚度', 'mm', '≥4.5', '4.6']),
            row('technical', B, ['1.1', '绝缘厚度', 'mm', '≥4.5', '4.7'])]
    source = {'rows': data, 'warnings': [], 'testData': False}
    preview = service.save_preview(7, ID, service.reconcile([source]))
    snapshot = service.confirm(preview['id'], 7, ID, {})
    doc = Document()
    doc.add_heading('技术特性参数表', 1)
    t = doc.add_table(rows=1, cols=5)
    for c, h in zip(t.rows[0].cells, ['序号', '名称', '单位', '招标人要求值', '投标人保证值']):
        c.text = h
    t.add_row().cells[0].text = '1'
    note = t.add_row()
    note.cells[0].merge(note.cells[-1]).text = '编制说明：保持本段原文。'
    doc.add_heading('下一章节', 1)
    path = stored / 'result.docx'
    doc.save(path)
    assert service.fill_document(path, snapshot['id'], ID, 'technical') == []
    actual = Document(path)
    assert len(actual.tables) == 2
    assert [t.cell(1, 4).text for t in actual.tables] == ['4.6', '4.7']
    assert all(t.cell(0, 3).text == '招标人要求值' for t in actual.tables)
    assert all('保持本段原文' in t.rows[-1].cells[0].text for t in actual.tables)
    assert actual.paragraphs[-1].text == '下一章节'


def test_no_confirmation_or_no_matching_table_never_invents_section(stored):
    data = service.save_preview(1, ID, service.reconcile([{'rows': [quote()], 'warnings': [], 'testData': False}]))
    path = stored / 'output.docx'
    doc = Document()
    doc.add_paragraph('只有技术方案正文')
    doc.save(path)
    with pytest.raises(ValueError, match='未确认'):
        service.fill_document(path, data['id'], ID, 'full')
    selected = service.confirm(data['id'], 1, ID, {})
    assert '未自动覆盖' in service.fill_document(path, selected['id'], ID, 'full')[0]
    assert len(Document(path).tables) == 0


SAMPLE = Path('F:/code/python/fengxuanAgent/deliverables')


@pytest.mark.skipif(not SAMPLE.exists(), reason='用户提供的本地样本不可用')
def test_user_excel_and_word_field_level_regression():
    excel = next(SAMPLE.glob('SL2639*表格数据*.xlsx'))
    word = next(SAMPLE.glob('SL2639*投标报价及技术表格*.docx'))
    a, b = service.parse_file(excel, excel.name), service.parse_file(word, word.name)
    expected = {'quote': 10, 'analysis': 80, 'technical': 689, 'component': 150, 'techDeviation': 3, 'bizDeviation': 3}
    assert Counter(r['kind'] for r in a['rows']) == expected
    assert Counter(r['kind'] for r in b['rows']) == expected
    first = next(r for r in a['rows'] if r['kind'] == 'quote')
    assert first['code'] == A
    assert first['values'][2:5] == ['10kV电力电缆,YJV22,3×70,无阻水', '千米', '8.5']
    assert first['values'][5:10] == ['203000', '1725500', '13', '229390', '1949815']
    result = service.reconcile([a, b])
    assert result['checks'] == []
    assert len(result['conflicts']) == 1
    assert result['conflicts'][0]['fields'] == ['投标文件响应']


@pytest.mark.skipif(not SAMPLE.exists(), reason='用户提供的本地样本不可用')
def test_actual_chapter_six_preserves_format_and_fills_all_families(stored):
    source = Path('F:/人民电缆/包1_完整招标文件_28934092821237684/SL2639-招标文件第2-6章.docx')
    if not source.exists():
        pytest.skip('招标格式样本不可用')
    excel = next(SAMPLE.glob('SL2639*表格数据*.xlsx'))
    imported = service.parse_file(excel, excel.name)
    preview = service.save_preview(7, ID, service.reconcile([imported]))
    selected = service.confirm(preview['id'], 7, ID, {})
    doc = Document(source)
    nodes = list(doc.element.body)
    starts = [i for i, n in enumerate(nodes) if n.tag == qn('w:p') and '第六章' in ''.join(t.text or '' for t in n.iter(qn('w:t'))) and '投标文件格式' in ''.join(t.text or '' for t in n.iter(qn('w:t')))]
    for node in nodes[:starts[-1]]:
        doc.element.body.remove(node)
    path = stored / 'actual-filled.docx'
    doc.save(path)
    warnings = service.fill_document(path, selected['id'], ID, 'full')
    assert not any('未' in w for w in warnings if '测试' not in w)
    actual = Document(path)
    kinds = Counter(service.family('', service.matrix(t._tbl)) for t in actual.tables)
    assert kinds['quote'] == 1
    assert kinds['analysis'] == kinds['technical'] == kinds['component'] == 10
    quotes = next(t for t in actual.tables if service.family('', service.matrix(t._tbl)) == 'quote')
    assert quotes.cell(2, 2).text == '10kV电力电缆,YJV22,3×70,无阻水'
    assert quotes.cell(2, 9).text == '1949815'
    tech = next(t for t in actual.tables if service.family('', service.matrix(t._tbl)) == 'technical')
    assert tech.cell(2, 3).text == '招标人要求值'
    assert tech.cell(3, 1).text == '产品型号规格'
    assert tech.cell(3, 4).text == 'YJV22'
    assert '编制说明' in tech.rows[-1].cells[0].text
    analysis = next(t for t in actual.tables if service.family('', service.matrix(t._tbl)) == 'analysis')
    totals = {r.cells[1].text: r.cells[7].text for r in analysis.rows if len(r.cells) == 9 and ('合计金额' in r.cells[1].text or r.cells[1].text == '总价')}
    # The original tender format explicitly denominates this table in 万元.
    assert '万元' in analysis.cell(0, 0).text
    assert service.number(totals['合计金额（单位完整货物单价）']) == service.Decimal('22.939')
    assert service.number(totals['总价']) == service.Decimal('194.9815')


def test_wrong_project_and_duplicate_rows_block_confirmation(stored):
    source = {'rows': [quote(), quote()], 'warnings': ['重复键：quote'], 'testData': False}
    preview = service.save_preview(7, ID, service.reconcile([source]))
    with pytest.raises(ValueError):
        service.confirm(preview['id'], 7, ID, {})


def test_nonfinite_numbers_are_invalid():
    assert service.number('NaN') is None
    assert service.number('Infinity') is None


@pytest.mark.asyncio
@pytest.mark.skipif(not SAMPLE.exists(), reason='用户提供的本地样本不可用')
async def test_word_only_preview_confirm_api_has_correct_total(stored, monkeypatch):
    from io import BytesIO
    from types import SimpleNamespace
    from fastapi import UploadFile
    from api.routers import bid_table_import as route
    monkeypatch.setattr(route, 'load_invitation_text', lambda value: '招标编号：SL2639')
    p = next(SAMPLE.glob('SL2639*投标报价及技术表格*.docx'))
    user = SimpleNamespace(id=7)
    result = await route.preview(user, [UploadFile(filename=p.name, file=BytesIO(p.read_bytes()))], ID)
    preview = result['data']
    assert preview['counts']['quote'] == 10
    with pytest.raises(Exception, match='确认采用'):
        await route.confirm(preview['id'], route.ConfirmIn(invitationId=ID), user)
    selected = await route.confirm(preview['id'], route.ConfirmIn(invitationId=ID, acknowledged=True), user)
    assert service.Decimal(selected['data']['bidPriceYuan']) == service.Decimal('16224867.70')
    snapshot = service.load_import(selected['data']['id'])
    assert snapshot['confirmed'] is True
    assert snapshot['testData'] is True


def test_prices_excluded_from_technical_volume(stored):
    source = {'rows': [quote()], 'warnings': [], 'testData': False}
    preview = service.save_preview(7, ID, service.reconcile([source]))
    snapshot = service.confirm(preview['id'], 7, ID, {})
    doc = Document()
    doc.add_heading('技术方案', 1)
    path = stored / 'technical.docx'
    doc.save(path)
    assert service.fill_document(path, snapshot['id'], ID, 'technical') == []
    assert Document(path).paragraphs[0].text == '技术方案'
    assert len(Document(path).tables) == 0


def test_fill_reports_actual_tables_without_changing_legacy_return(stored):
    from api.services.cablepkg.form_reconcile import table_digest
    source = {'rows': [row('techDeviation', '', ['1', '交货时间', '合同约定时间', '按合同约定交货', '无偏差'])], 'warnings': [], 'testData': False}
    preview = service.save_preview(7, ID, service.reconcile([source]))
    selected = service.confirm(preview['id'], 7, ID, {})
    doc = Document()
    doc.add_heading('技术偏差表', 1)
    table = doc.add_table(rows=2, cols=5)
    for cell, value in zip(table.rows[0].cells, ['序号', '偏差事项', '招标文件要求', '投标文件响应', '偏差说明']):
        cell.text = value
    table.cell(1, 0).text = '1'
    path = stored / 'receipt.docx'
    doc.save(path)
    outcomes = []
    assert service.fill_document(path, selected['id'], ID, 'full', outcomes=outcomes) == []
    actual = Document(path)
    assert [[c.text for c in r.cells] for r in actual.tables[0].rows][1] == ['1', '交货时间', '合同约定时间', '按合同约定交货', '无偏差']
    assert outcomes == [{'kind': 'techDeviation', 'status': 'filled', 'codes': [''], 'tableDigests': [table_digest(actual.tables[0]._tbl)]}]


def test_fill_missing_target_does_not_report_success(stored):
    source = {'rows': [quote()], 'warnings': [], 'testData': False}
    preview = service.save_preview(7, ID, service.reconcile([source]))
    selected = service.confirm(preview['id'], 7, ID, {})
    path = stored / 'no-table.docx'
    Document().save(path)
    outcomes = []
    warnings = service.fill_document(path, selected['id'], ID, 'full', outcomes=outcomes)
    assert '未自动覆盖' in warnings[0]
    assert outcomes == [{'kind': 'quote', 'status': 'not_written', 'codes': [A], 'tableDigests': []}]
