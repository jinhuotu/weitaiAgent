"""Deterministic, reviewable imports of cable bid response tables."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import json
from pathlib import Path
import re
from uuid import uuid4

from docx import Document
from docx.oxml.ns import qn
from openpyxl import load_workbook
from common.config import get_settings

LABELS = {'quote': '货物报价', 'analysis': '单价分析', 'technical': '技术参数',
          'component': '组件配置', 'techDeviation': '技术偏差', 'bizDeviation': '商务偏差'}
HEADERS = {
    'quote': ['项目单位', '项目名称', '物资描述', '单位', '数量', '未含税单价', '未含税合价', '税率', '含税单价', '含税合价'],
    'analysis': ['序号', '组件材料项目名称', '产地', '制造商', '单位', '数量', '单价', '合价', '备注'],
    'technical': ['序号', '名称', '单位', '招标人要求值', '投标人保证值'],
    'component': ['序号', '元件名称', '型式规格，参数', '单位', '数量', '型式、规格', '数量', '制造商', '原产地'],
    'techDeviation': ['序号', '偏差事项', '招标文件要求', '投标文件响应', '偏差说明'],
    'bizDeviation': ['序号', '偏差事项', '招标文件要求', '投标文件响应', '偏差说明'],
}
CODE = re.compile(r'\b\d{4}-\d{9}-\d{5}\b')


def text(value):
    return '' if value is None else str(value).strip()


def norm(value):
    return re.sub(r'[\s（）(),，：:]', '', text(value))


def number(value):
    try:
        parsed = Decimal(text(value).replace(',', '').replace('，', ''))
        return parsed if parsed.is_finite() else None
    except InvalidOperation:
        return None


def equivalent(left, right):
    a, b = number(left), number(right)
    return a == b if a is not None and b is not None else norm(left) == norm(right)


def family(title, rows):
    joined = '|'.join(text(x) for r in rows[:6] for x in r)
    if '投标人保证值' in joined:
        return 'technical'
    if '原产地' in joined and ('元件名称' in joined or '货物组件' in title):
        return 'component'
    if '偏差事项' in joined or ('偏差说明' in joined and '投标文件条款' in joined):
        return 'bizDeviation' if '商务' in title else 'techDeviation' if '技术' in title else None
    if '组件材料项目名称' in joined:
        return 'analysis'
    if ('物资描述' in joined or '货物（功能规格）描述' in joined) and '数量' in joined and ('含税' in joined or '税率' in joined):
        return 'quote'
    return None


def matrix(table):
    """Expand horizontal merges, preserving the physical grid for two-level heads."""
    result = []
    for tr in table.findall(qn('w:tr')):
        row = []
        for tc in tr.findall(qn('w:tc')):
            value = ''.join(n.text or '' for n in tc.iter(qn('w:t')))
            span = tc.find('w:tcPr/w:gridSpan', tc.nsmap)
            row.extend([value] * (int(span.get(qn('w:val'))) if span is not None else 1))
        result.append(row)
    return result


def parse_file(path: Path, filename: str):
    import zipfile
    with zipfile.ZipFile(path) as archive:
        if sum(info.file_size for info in archive.infolist()) > 150 * 1024 * 1024:
            raise ValueError('文件解压后过大')
    result = {'file': filename, 'rows': [], 'warnings': [], 'identity': {}, 'testData': '测试' in filename}
    blocks = []
    descriptions = {}
    if path.suffix.lower() == '.xlsx':
        wb = load_workbook(path, read_only=True, data_only=True)
        try:
            for ws in wb:
                blocks.append((ws.title, [[text(v) for v in r] for r in ws.values], ''))
        finally:
            wb.close()
    else:
        doc = Document(path)
        context, title = '', ''
        for node in doc.element.body:
            if node.tag == qn('w:p'):
                line = ''.join(n.text or '' for n in node.iter(qn('w:t')))
                tender = re.search(r'招标(?:编号|号)\s*[：:]\s*([A-Za-z]+\d+)', line)
                if tender:
                    result['identity']['招标编号'] = tender.group(1)
                result['testData'] |= '模拟值' in line or '测试数据' in line
                if CODE.search(line):
                    context = CODE.search(line).group()
                    match = re.search(r'物资描述[：:]\s*(.*?)\s*技术规范ID', line)
                    if match:
                        descriptions[norm(match.group(1))] = context
                if any(t in line for t in ['偏差表', '参数表', '配置表', '单价分析表', '报价汇总表']):
                    title = line
            elif node.tag == qn('w:tbl'):
                blocks.append((title, matrix(node), context))
    for title, rows, context in blocks:
        for raw in rows[:40]:
            if len(raw) == 2 or title.startswith('00'):
                if len(raw) >= 2 and norm(raw[0]) in {'招标编号', '分标编号', '包号', '投标人'} and raw[1]:
                    result['identity'][norm(raw[0])] = raw[1]
        kind = family(title, rows)
        if not kind:
            continue
        header = next((i for i, r in enumerate(rows[:8]) if (
            ('名称' in r and '投标人保证值' in r) if kind == 'technical' else
            ('组件材料项目名称' in r) if kind == 'analysis' else
            ('元件名称' in r) if kind == 'component' else
            ('偏差事项' in r) if kind.endswith('Deviation') else '物资描述' in r
        )), None)
        if header is None:
            result['warnings'].append(f'{title}：未找到完整表头，未导入')
            continue
        heads = rows[header]
        excel = path.suffix.lower() == '.xlsx'
        for rownum, raw in enumerate(rows[header + 1:], header + 2):
            raw = [text(v) for v in raw]
            if not any(raw) or any(v.startswith('=') for v in raw):
                continue
            def get(name):
                return raw[heads.index(name)] if name in heads and heads.index(name) < len(raw) else ''
            code = get('技术规范ID') or context
            if kind == 'quote':
                if excel:
                    vals = [get(n) for n in ['项目单位', '项目名称', '物资描述', '单位', '数量', '未含税单价（元）', '未含税合价（元）', '税率（%）', '含税单价（元）', '含税合价（元）']]
                else:
                    vals = (raw + [''] * 10)[:10]
                    code = descriptions.get(norm(vals[2]), '')
                if not vals[2] or not code or number(vals[4]) is None:
                    continue
                key = code
            elif kind == 'technical':
                vals = [get(n) for n in HEADERS[kind]]
                if not code or not re.fullmatch(r'\d+(?:\.\d+)*', vals[0]) or not vals[1]:
                    continue
                key = f'{code}|{vals[0]}|{norm(vals[1])}|{norm(vals[2])}'
            elif kind == 'component':
                if excel:
                    vals = [get(n) for n in ['序号', '元件名称', '型式规格，参数（项目货物需求）', '单位', '数量（项目需求）', '型式、规格（投标人响应）', '数量（投标人响应）', '制造商', '原产地']]
                else:
                    if len(raw) == 10:
                        raw = raw[:5] + raw[6:]
                    vals = (raw + [''] * 9)[:9]
                    if len(set(raw)) == 1:
                        vals = ['', raw[0], '', '', '', '', '', '', '']
                group_heading = bool(re.match(r'\d+[、.]', vals[1]))
                if not code or not vals[1] or not (re.fullmatch(r'\d+(?:\.\d+)*', vals[0]) or group_heading):
                    continue
                key = f'{code}|{vals[0]}|{norm(vals[1])}'
            elif kind == 'analysis':
                vals = [get(n) for n in ['序号', '组件材料项目名称', '产地', '制造商', '单位', '数量', '单价（含税，元）', '合价（含税，元）', '备注']] if excel else (raw + [''] * 9)[:9]
                if not code or not vals[1] or vals[1] == '组件材料项目名称':
                    continue
                if any(s in vals[1] for s in ['合计金额', '总价']):
                    continue
                if not excel and '万元' in ''.join(rows[0]):
                    for index in [6, 7]:
                        amount = number(vals[index])
                        if amount is not None:
                            vals[index] = format(amount * Decimal(10000), 'f')
                key = f'{code}|{norm(vals[1])}'
            else:
                vals = [get(n) for n in HEADERS[kind]]
                code = ''
                if not vals[1] or not re.fullmatch(r'\d+', vals[0]):
                    continue
                key = norm(vals[1])
            result['rows'].append({'key': f'{kind}|{key}', 'kind': kind, 'code': code,
                                   'values': vals, 'source': f'{filename} / {title} / 行{rownum}'})
    seen = set()
    for row in result['rows']:
        if row['key'] in seen:
            result['warnings'].append(f'重复键：{row["key"]}，请拆分或修正来源文件')
        seen.add(row['key'])
    if not result['rows']:
        result['warnings'].append('未识别到支持的投标填报表格')
    return result


def reconcile(sources):
    rows, conflicts, warnings = {}, [], []
    for source in sources:
        warnings.extend(source['warnings'])
        for row in source['rows']:
            old = rows.get(row['key'])
            if old:
                fields = [i for i, (a, b) in enumerate(zip(old['values'], row['values'])) if not equivalent(a, b)]
                if fields:
                    conflicts.append({'key': row['key'], 'kind': row['kind'], 'left': old, 'right': row,
                                      'fields': [HEADERS[row['kind']][i] for i in fields]})
            else:
                rows[row['key']] = row
    single_sided = []
    if len(sources) == 2:
        a, b = ({r['key'] for r in s['rows']} for s in sources)
        if a != b:
            warnings.append(f'两份文件存在单边数据：第一份独有 {len(a-b)} 条，第二份独有 {len(b-a)} 条，预览按并集保留。')
            single_sided = [r for s in sources for r in s['rows'] if r['key'] in a ^ b]
    merged = list(rows.values())
    identity = {}
    for source in sources:
        for key, value in source.get('identity', {}).items():
            if key in identity and not equivalent(identity[key], value):
                warnings.append(f'身份不一致：{key}，请确认两份文件属于同一项目包')
            identity[key] = value
    return {'rows': merged, 'columns': HEADERS, 'conflicts': conflicts, 'identity': identity, 'singleSided': single_sided, 'warnings': warnings,
            'counts': dict(Counter(r['kind'] for r in merged)),
            'testData': any(s['testData'] for s in sources), 'checks': validate(merged)}


def validate(rows):
    problems = []
    quotes = {r['code']: r for r in rows if r['kind'] == 'quote'}
    for row in rows:
        v = row['values']
        if row['kind'] == 'quote':
            numbers = [number(v[i]) for i in [4, 5, 6, 7, 8, 9]]
            if any(n is None for n in numbers):
                problems.append(f'{row["code"]}：数量或报价缺少有效数值')
                continue
            qty, net, net_total, rate, gross, total = numbers
            if qty <= 0 or min(net, net_total, gross, total) < 0 or not 0 <= rate <= 100:
                problems.append(f'{row["code"]}：数量、价格或税率超出有效范围')
            cents = lambda n: n.quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
            if cents(qty * net) != cents(net_total) or cents(net * (1 + rate / 100)) != cents(gross) or cents(qty * gross) != cents(total):
                problems.append(f'{row["code"]}：数量、税率与报价金额不一致')
        if row['kind'] == 'technical' and (not v[4] or norm(v[4]) in {'响应', '完全响应', '满足', '满足要求'}):
            problems.append(f'{row["code"]} / {v[1]}：保证值为空或为空泛承诺')
        if row['kind'] == 'analysis':
            quantity, price, amount = (number(v[i]) for i in [5, 6, 7])
            if quantity is not None and price is not None and amount is not None:
                if abs((quantity * price).quantize(Decimal('.01'), rounding=ROUND_HALF_UP) - amount) > Decimal('.01'):
                    problems.append(f'{row["code"]} / {v[1]}：单价分析数量乘单价不等于合价')
        if quotes and row['code'] and row['code'] not in quotes:
            problems.append(f'{row["code"]}：未在货物报价中找到对应物资')
    for code, quote in quotes.items():
        parts = [r for r in rows if r['kind'] == 'analysis' and r['code'] == code]
        if parts:
            totals = [number(r['values'][7]) for r in parts]
            if any(v is None for v in totals):
                problems.append(f'{code}：单价分析合价存在空值')
            elif number(quote['values'][8]) is not None and abs(sum(totals) - number(quote['values'][8])) > Decimal('.01'):
                problems.append(f'{code}：单价分析合计与清单含税单价不一致')
    return problems


def store_root():
    root = Path(get_settings().storage_root).resolve() / 'bid-table-imports'
    root.mkdir(parents=True, exist_ok=True)
    return root


def save_preview(owner, invitation_id, preview):
    token = uuid4().hex
    data = {**preview, 'id': token, 'owner': int(owner), 'invitationId': invitation_id, 'confirmed': False}
    (store_root() / f'{token}.json').write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    return data


def load_import(token):
    if not re.fullmatch(r'[a-f0-9]{32}', token or ''):
        raise ValueError('无效导入编号')
    return json.loads((store_root() / f'{token}.json').read_text(encoding='utf-8'))


def confirm(token, owner, invitation_id, choices):
    data = load_import(token)
    if data['owner'] != int(owner) or data['invitationId'] != invitation_id:
        raise ValueError('导入不属于当前用户或当前招标资料')
    if data['warnings'] and any('重复键' in w or '未识别' in w or '身份不一致' in w for w in data['warnings']):
        raise ValueError('请先处理重复键或无法识别的文件')
    rows = {r['key']: r for r in data['rows']}
    for conflict in data['conflicts']:
        choice = choices.get(conflict['key'])
        if choice not in {'left', 'right'}:
            raise ValueError('请逐项选择差异采用来源')
        rows[conflict['key']] = conflict[choice]
    checks = validate(list(rows.values()))
    if checks:
        raise ValueError('请修正数据后重新导入：' + '；'.join(checks[:5]))
    # Confirmation creates an immutable snapshot; later previews cannot change it.
    snapshot = {**data, 'id': uuid4().hex, 'rows': list(rows.values()), 'choices': choices, 'confirmed': True, 'checks': []}
    (store_root() / f'{snapshot["id"]}.json').write_text(json.dumps(snapshot, ensure_ascii=False), encoding='utf-8')
    return snapshot


def fill_document(path: Path, token: str, invitation_id: str, volume: str, *, outcomes: list | None = None):
    if not token:
        return []
    data = load_import(token)
    if not data.get('confirmed') or data['invitationId'] != invitation_id:
        raise ValueError('填报数据未确认或与本次招标不一致')
    doc = Document(path)
    available = {}
    for row in data['rows']:
        if volume == 'business' and row['kind'] in {'technical', 'component', 'techDeviation'}:
            continue
        if volume == 'technical' and row['kind'] in {'quote', 'analysis', 'bizDeviation'}:
            continue
        available.setdefault(row['kind'], {}).setdefault(row['code'], []).append(row)
    targets = {}
    title = ''
    from api.services.tenders.directory_guard import directory_blocks
    protected_directory = directory_blocks(doc)
    for node in doc.element.body:
        if node in protected_directory:
            continue
        if node.tag == qn('w:p'):
            s = ''.join(n.text or '' for n in node.iter(qn('w:t')))
            if any(t in s for t in ['偏差表', '参数表', '配置表', '单价分析表', '报价汇总表']):
                title = s
        elif node.tag == qn('w:tbl'):
            kind = family(title, matrix(node))
            if kind in available:
                targets.setdefault(kind, []).append(node)
    warnings = []
    for kind, groups in available.items():
        outcome = {'kind': kind, 'status': 'not_written', 'codes': list(groups), 'tableDigests': []}
        if outcomes is not None:
            outcomes.append(outcome)
        sites = targets.get(kind, [])
        if len(sites) != 1:
            warnings.append(f'{LABELS[kind]}：对应格式表 {len(sites)} 张，无法唯一定位，未自动覆盖')
            continue
        original = sites[0]
        raw = matrix(original)
        from docx.table import Table
        header = next((i for i, r in enumerate(raw[:8]) if (
            '投标人保证值' in r and '名称' in r if kind == 'technical' else
            '元件名称' in r if kind == 'component' else
            '组件材料项目名称' in r if kind == 'analysis' else
            '偏差说明' in r if kind.endswith('Deviation') else
            ('物资描述' in r or '货物（功能规格）描述' in r)
        )), None)
        if header is None:
            warnings.append(f'{LABELS[kind]}：表头不兼容，未写入')
            continue
        end_header = header + (2 if kind == 'quote' and '单价' in raw[header + 1] else 1)
        physical_map = list(range(len(HEADERS[kind])))
        if kind == 'component' and len(raw[header]) == 10:
            physical_map = [0, 1, 2, 3, 4, 4, 5, 6, 7, 8]
        if len(raw[header]) != len(physical_map):
            warnings.append(f'{LABELS[kind]}：列结构不兼容，未写入')
            continue
        body_rows = original.tr_lst[end_header:]
        notes = [r for r in body_rows if re.match(r'编制说明|填写说明|投标人声明', ''.join(n.text or '' for n in r.iter(qn('w:t'))))]
        summaries = [r for r in body_rows if r not in notes and kind in {'quote', 'analysis'}
                     and re.search(r'合计|总价', ''.join(n.text or '' for n in r.iter(qn('w:t'))))]
        samples = [r for r in body_rows if r not in notes and r not in summaries]
        if not samples:
            warnings.append(f'{LABELS[kind]}：没有可用数据行模板，未写入')
            continue
        sample = max(samples, key=lambda r: len(r.tc_lst))
        # Populated business values are not disposable template placeholders.
        populated = any(
            any(CODE.search(v) for v in r) or
            (kind == 'technical' and len(r) >= 5 and r[4] and not re.search(r'填写|填报|保证值|待补充|待完善|^—$|^/$', r[4]))
            for r in raw[end_header:] if r and not re.match(r'编制说明|填写说明|投标人声明', r[0])
        )
        if populated:
            warnings.append(f'{LABELS[kind]}：格式表已有数据，未覆盖，请人工核对')
            continue
        from api.services.cablepkg.form_pagination import prepare_chapter, paginate_group
        chapter = prepare_chapter(doc, original, kind, excluded=protected_directory)
        cursor = original
        written = []
        output_groups = {'': [r for records in groups.values() for r in records]} if kind == 'quote' else groups
        for group_index, (code, records) in enumerate(output_groups.items()):
            clone = deepcopy(original)
            for tr in list(clone.tr_lst)[end_header:]:
                clone.remove(tr)
            new = Table(clone, doc._body)
            in_wan = kind == 'analysis' and '万元' in ''.join(raw[0])
            quoted = next((r for r in data['rows'] if r['kind'] == 'quote' and r['code'] == code), None)
            for record in records:
                values = list(record['values'])
                if kind.endswith('Deviation') and '招标文件条目号' in raw[header]:
                    values = [values[0], '', values[1] + '：' + values[2], values[3], values[4]]
                if in_wan:
                    for i in [6, 7]:
                        amount = number(values[i])
                        if amount is not None:
                            values[i] = format(amount / Decimal(10000), 'f')
                tr = deepcopy(sample)
                clone.append(tr)
                from docx.table import _Cell
                grid = 0
                for tc in tr.tc_lst:
                    index = physical_map[grid]
                    _Cell(tc, new).text = values[index]
                    span = tc.find('w:tcPr/w:gridSpan', tc.nsmap)
                    grid += int(span.get(qn('w:val'))) if span is not None else 1
            from docx.table import _Row
            for summary in summaries:
                copied = deepcopy(summary)
                clone.append(copied)
                summary_row = _Row(copied, new)
                caption = ''.join(n.text or '' for n in copied.iter(qn('w:t')))
                if kind == 'analysis':
                    amount = (number(quoted['values'][9 if '总价' in caption else 8]) if quoted else
                              sum((number(r['values'][7]) or Decimal(0) for r in records), Decimal(0)) if '总价' not in caption else None)
                    if amount is not None:
                        summary_row.cells[7].text = format(amount / (10000 if in_wan else 1), 'f')
                    else:
                        warnings.append(f'{code}：缺少货物报价数量，单价分析总价未填写')
                else:
                    for column in [4, 6, 9]:
                        cell = summary_row.cells[column]
                        if not re.search(r'合计|总价', cell.text):
                            cell.text = format(sum((number(r['values'][column]) or Decimal(0) for r in records), Decimal(0)), 'f')
            for note in notes:
                clone.append(deepcopy(note))
            label = None
            if code:
                description = ('物资描述：' + quoted['values'][2] + '    ') if quoted else ''
                label = doc.add_paragraph(description + f'技术规范ID：{code}' + ('（测试数据）' if data['testData'] else ''))
                cursor.addnext(label._p)
                cursor = label._p
            cursor.addnext(clone)
            cursor = clone
            paginate_group(doc, clone, kind=kind, header_end=end_header,
                           label=label._p if label is not None else None,
                           group_index=group_index, chapter=chapter)
            written.append(clone)
        original.getparent().remove(original)
        from api.services.cablepkg.form_reconcile import table_digest
        outcome.update(status='filled', tableDigests=[table_digest(node) for node in written])
    if data['testData']:
        warnings.append('本次填报来源为测试数据，仅用于测试，不构成可投标证据')
    doc.save(path)
    return warnings
