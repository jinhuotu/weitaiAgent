"""Remove generated missing-form frames only when local table content proves placement.

This is a document-layout reconciliation, never a compliance/evidence decision.
Original tables, source instructions and missing supporting attachments are untouched.
"""
from collections import Counter
import hashlib
import re

from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph


_TITLES = {
    '技术偏差表': 'techDeviation', '商务偏差表': 'bizDeviation',
    '技术特性参数表': 'technical', '技术参数表': 'technical',
    '货物组件材料配置表': 'component', '组件配置表': 'component',
    '货物清单单价分析表': 'analysis', '单价分析表': 'analysis',
    '货物报价汇总表': 'quote', '货物报价表': 'quote',
}
_PREFIX = re.compile(r'^(?:第[一二三四五六七八九十百0-9]+[章节]|[（(][一二三四五六七八九十0-9]+[）)]|[一二三四五六七八九十]+[、.．]|\d+(?:\.\d+)*[、.．])')


def text(node):
    return ''.join(t.text or '' for t in node.iter(qn('w:t'))).strip()


def title_kind(value):
    value = re.sub(r'\s+', '', value or '')
    while True:
        stripped = _PREFIX.sub('', value, count=1)
        if stripped == value:
            break
        value = stripped
    value = re.sub(r'[（(]上传投标工具路径[：:].*$', '', value)
    value = re.sub(r'[（(](?:格式|如有)[）)]$', '', value).strip('：:')
    return _TITLES.get(value)


def table_digest(node):
    """Fingerprint the actual table grid and text across DOCX save/reload."""
    rows = [[cell.text for cell in row.cells] for row in Table(node, None).rows]
    import json
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode('utf-8')).hexdigest()


def _heading(document, node):
    if node.tag != qn('w:p'):
        return False
    value = text(node)
    if not value or len(value) >= 100:
        return False
    if value in {'商务文件', '技术文件', '价格文件', '报价文件'} or any(
            mark.get(qn('w:name'), '').startswith('toc_chapter_native_')
            for mark in node.iter(qn('w:bookmarkStart'))):
        return True
    para = Paragraph(node, document._body)
    style = para.style
    seen = set()
    while style is not None and style.style_id not in seen:
        seen.add(style.style_id)
        if re.match(r'Heading|标题|TOC|目录', style.name or '', re.I):
            return True
        props = style.element.find(qn('w:pPr'))
        if props is not None and props.find(qn('w:outlineLvl')) is not None:
            return True
        style = style.base_style
    return bool(node.find('w:pPr/w:outlineLvl', node.nsmap) is not None
                or _PREFIX.match(re.sub(r'\s+', '', value)) or title_kind(value))


def _placeholder(node):
    if node.tag != qn('w:p') or node.find('w:pPr/w:pBdr', node.nsmap) is None:
        return None
    if any(next(node.iter(qn('w:' + tag)), None) is not None
           for tag in ('drawing', 'pict', 'object', 'sectPr')):
        return None
    match = re.fullmatch(r'对应\s*(.*?)；所需材料：\s*(.*)', text(node))
    if not match:
        return None
    kind = title_kind(match[1])
    if not kind or kind != title_kind(match[2]):
        return None
    return kind, match[2]


def _deviation_complete(node):
    rows = [[re.sub(r'\s+', '', cell.text) for cell in row.cells]
            for row in Table(node, None).rows]
    header = next((i for i, row in enumerate(rows[:8]) if len(row) == 5
                   and row[0] == '序号' and '偏差说明' in row
                   and ('投标文件响应' in row or '投标文件条款' in row)
                   and ('招标文件要求' in row or '招标文件条款' in row)), None)
    if header is None:
        return False
    response = next(i for i, value in enumerate(rows[header])
                    if value in {'投标文件响应', '投标文件条款'})
    records = []
    for row in rows[header + 1:]:
        if not any(row) or row[0].startswith(('投标人声明', '编制说明', '填写说明')):
            continue
        if len(row) != 5 or not re.fullmatch(r'\d+', row[0]):
            return False
        # The item-number column in the original business format may be blank.
        neutral = (row[4] == '无偏差' and row[response] in {'', '—', '-'}
                   and ((row[1] == '其他' and row[2] in {'—', '-'})
                        or row[2] in {'其他：—', '其他:—', '其他：-', '其他:-'}))
        if neutral:
            continue
        required = [row[2], row[response]]
        if rows[header][1] == '偏差事项':
            required.append(row[1])
        if any(not value or re.search(r'填写|填报|待补|待完善|在此|[_＿]{2,}|[〔【].*[〕】]', value)
               or value in {'/', '—', '-', '无', '无偏差'} for value in required):
            return False
        if not row[4] or re.search(r'填写|填报|待补|[_＿]{2,}', row[4]):
            return False
        records.append(row)
    return bool(records)



def form_structure_matches(node, kind):
    """A format exists independently of whether its response data is filled."""
    rows = [[re.sub(r'\s+', '', cell.text) for cell in row.cells]
            for row in Table(node, None).rows[:8]]
    for row in rows:
        cells = set(row)
        joined = '|'.join(row)
        if kind in {'techDeviation', 'bizDeviation'}:
            if ('序号' in cells and '偏差说明' in cells
                    and ('投标文件响应' in cells or '投标文件条款' in cells)
                    and ('招标文件要求' in cells or '招标文件条款' in cells)):
                return True
        elif kind == 'analysis':
            if {'序号', '组件材料项目名称', '单位', '数量'} <= cells and '单价' in joined and '合价' in joined:
                return True
        elif kind == 'technical':
            if {'序号', '名称', '单位', '招标人要求值', '投标人保证值'} <= cells:
                return True
        elif kind == 'component':
            if {'序号', '元件名称', '单位', '制造商', '原产地'} <= cells:
                return True
        elif kind == 'quote':
            if ('物资描述' in joined or '货物（功能规格）描述' in joined) and '数量' in joined and ('含税' in joined or '税率' in joined):
                return True
    return False


def form_at_heading(document, head, kind, *, excluded=()):
    """Find one actual format under this heading, never across another chapter."""
    blocked = set(excluded)
    if head in blocked or title_kind(text(head)) != kind:
        return False
    tables = []
    cursor = head.getnext()
    while cursor is not None and cursor.tag != qn('w:sectPr'):
        if cursor in blocked or (_heading(document, cursor) and title_kind(text(cursor)) != kind):
            break
        if cursor.tag == qn('w:tbl') and form_structure_matches(cursor, kind):
            tables.append(cursor)
        cursor = cursor.getnext()
    return len(tables) == 1


def reconcile_form_placeholders(document, outcomes=(), *, excluded=()):
    """Delete only same-region missing-form frames; ambiguous matches stay put.

    An existing format satisfies only the need for a form, not its data or
    compliance. Incomplete cells and failed imports remain separate warnings.
    Headings alone, cover-only fills and other regions cannot satisfy a form.
    Call only after all document writers have finished.
    """
    excluded = set(excluded)
    nodes = list(document.element.body)
    receipts = {}
    for outcome in outcomes:
        if outcome.get('status') == 'filled' and outcome.get('tableDigests'):
            receipts.setdefault(outcome['kind'], []).append(Counter(outcome['tableDigests']))
    removed = []
    for index, node in enumerate(nodes):
        candidate = _placeholder(node)
        if candidate is None or node in excluded:
            continue
        kind, title = candidate
        # Stop at a different chapter, unknown heading or protected directory.
        start = index
        while start > 0:
            previous = nodes[start - 1]
            if previous in excluded or (_heading(document, previous) and title_kind(text(previous)) != kind):
                break
            start -= 1
            if _heading(document, previous) and title_kind(text(previous)) == kind:
                break
        end = index + 1
        while end < len(nodes):
            following = nodes[end]
            if following in excluded or (_heading(document, following) and title_kind(text(following)) != kind):
                break
            end += 1
        region = nodes[start:end]
        # Require an actual local form heading; an isolated text mention is not enough.
        if not any(n.tag == qn('w:p') and _placeholder(n) is None
                   and title_kind(text(n)) == kind for n in region):
            continue
        tables = [n for n in region if n.tag == qn('w:tbl')]
        if not tables:
            continue
        actual = Counter(table_digest(n) for n in tables) if kind in receipts else Counter()
        imported = any(expected == actual for expected in receipts.get(kind, []))
        native = len(tables) == 1 and form_structure_matches(tables[0], kind)
        if not imported and not native:
            continue
        marks = [m.get(qn('w:name'), '') for m in node.iter(qn('w:bookmarkStart'))]
        removed.append({'kind': kind, 'title': title, 'markers': marks,
                        'source': 'table-import' if imported else 'existing-format'})
        node.getparent().remove(node)
    return removed
