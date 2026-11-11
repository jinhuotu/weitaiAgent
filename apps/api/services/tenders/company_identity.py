"""Company identity is input data, never a deployment-wide bidder constant."""
import json
import re

COMPANY_RE = re.compile(r'[\u4e00-\u9fffA-Za-z0-9·（）()]{2,60}(?:有限责任公司|股份有限公司|有限公司|事务所|合作社|研究院)')
BIDDER_RE = re.compile(r'(?:投标人|响应人|供应商|承包人|乙方|卖方)(?:名称|全称)?\s*[:：]\s*([^\n；;。]{2,100})')
_SUBJECT_LABEL = re.compile(
    r'^\s*(?P<role>致|招标人|采购人|发包人|业主|投标人|响应人|供应商|承诺人|承包人)'
    r'(?:名称|全称)?(?:\s*[（(][^）)\n]*[）)])?\s*[:：][:：\s]*'
)
_RECIPIENTS = {'致', '招标人', '采购人', '发包人', '业主'}
_SUBJECT_COMPANY = re.compile(COMPANY_RE.pattern.replace('|事务所', '|公司|集团|事务所'))
_BARE_ADDRESSEE = re.compile(r'^\s*(' + _SUBJECT_COMPANY.pattern + r')\s*[:：]')


def company_key(value):
    return re.sub(r'[\s　（）()]', '', str(value or '')).casefold()


def same_company(left, right, *, left_code='', right_code=''):
    left_code=re.sub(r'\s+','',str(left_code or '')).upper()
    right_code=re.sub(r'\s+','',str(right_code or '')).upper()
    if left_code and right_code:
        return left_code == right_code
    return bool(company_key(left) and company_key(left) == company_key(right))


def credit_codes_from_document(doc):
    from api.services.tenders.facts import load_facts
    values=[]
    for tag in getattr(doc,'tags',None) or []:
        if str(tag).startswith(('credit_code:','uscc:')):
            values.append(str(tag).split(':',1)[1])
    for item in (load_facts(getattr(doc,'summary','') or '') or {}).get('fields',[]):
        if item.get('name') in {'统一社会信用代码','社会信用代码'}:
            values.append(item.get('value',''))
    codes={re.sub(r'\s+','',str(value)).upper() for value in values}
    codes={value for value in codes if re.fullmatch(r'[0-9A-Z]{18}',value)}
    return codes


def credit_code_from_document(doc):
    codes=credit_codes_from_document(doc)
    return next(iter(codes)) if len(codes)==1 else ''


def named_bidders(text):
    names = []
    for match in BIDDER_RE.finditer(text or ''):
        raw = match.group(1).strip()
        if re.search(r'必须|应当|须为|须是|具备|应为|具有|资格|提供|提交|填写|签字|盖章|[_＿]', raw):
            continue
        company = COMPANY_RE.search(raw)
        if company:
            names.append(company.group())
    return list(dict.fromkeys(names))


def foreign_bidder(text, bidder_name=''):
    return any(not same_company(name, bidder_name) for name in named_bidders(text))


def subject_changes(text, brief):
    """Find company spans only in formal addressees and signature roles."""
    if not (brief.bidderName.strip() or brief.tenderer.strip()):
        return []
    changes = []
    for line in re.finditer(r'[^\r\n]+', text):
        label = _SUBJECT_LABEL.match(line[0])
        if label:
            match = _SUBJECT_COMPANY.match(line[0], label.end())
            expected = brief.tenderer if label['role'] in _RECIPIENTS else brief.bidderName
        else:
            match = _BARE_ADDRESSEE.match(line[0])
            expected = brief.tenderer
        if match is None:
            continue
        group = 0 if label else 1
        company = match[group]
        if same_company(company, expected) or (not label and same_company(company, brief.bidderName)):
            continue
        if not expected and same_company(company, brief.bidderName):
            continue
        changes.append((line.start() + match.start(group), line.start() + match.end(group), company, expected.strip()))
    return changes


def _subject_replacement(company, expected, location, repair):
    if not expected or not repair:
        from common.errors import AppError, ErrorCode

        raise AppError(ErrorCode.VALIDATION, f'{location}主体异常：{company}，请核对投标人与招标人', status_code=422)
    return f'{location}主体替换：{company} → {expected}'


def guard_draft_subjects(brief):
    """Check the content selected for generation before any renderer writes it."""
    notes = []

    def guarded(text, location):
        for start, end, company, expected in reversed(subject_changes(text, brief)):
            where = f'{location}第{text.count(chr(10), 0, start) + 1}行'
            notes.append(_subject_replacement(company, expected, where, True))
            text = text[:start] + expected + text[end:]
        return text

    items = []
    for item in brief.outlineItems:
        if not item.skipped:
            values = {field: guarded(getattr(item, field), f'大纲「{item.title}」{field}')
                      for field in ('body', 'draftBody')}
            if any(getattr(item, field) != value for field, value in values.items()):
                item = item.model_copy(update=values)
        items.append(item)
    for point in (brief.technicalPackage or {}).get('sections', []):
        if isinstance(point.get('body'), str):
            point['body'] = guarded(point['body'], f'技术要点「{point.get("title", "")}」')
    if notes:
        brief.outlineItems = items
    return notes


def guard_document_subjects(doc, brief, *, repair=False):
    from docx.oxml.ns import qn
    from docx.text.paragraph import Paragraph
    from api.services.tenders.document import _inline_nodes, _replace_inline_span

    notes = []
    for index, node in enumerate(doc.element.body.iter(qn('w:p')), 1):
        paragraph = Paragraph(node, doc)
        text = ''.join(value for _, value in _inline_nodes(paragraph))
        for start, end, company, expected in reversed(subject_changes(text, brief)):
            notes.append(_subject_replacement(company, expected, f'正文第{index}段', repair))
            _replace_inline_span(paragraph, start, end, expected)
    return notes


def missing_company_notes(brief):
    labels = {'bidderName':'投标人全称', 'bidderAddress':'投标人地址',
              'legalPersonName':'法定代表人姓名', 'legalPersonIdNo':'法定代表人证件号'}
    missing = [label for key,label in labels.items() if not str(getattr(brief,key,'') or '').strip()]
    return ['投标人资料缺少：' + '、'.join(missing) + '；已留空，未代入其他公司的资料'] if missing else []


def company_from_document(doc):
    """Prefer explicit ownership, then structured OCR metadata, never a filename default."""
    names = []
    for tag in getattr(doc, 'tags', None) or []:
        if str(tag).startswith('bidder:'):
            names.append(str(tag)[7:].strip())
    from api.services.tenders.facts import load_facts
    from api.services.tenders.performance import PERF_META_PREFIX
    raw = getattr(doc, 'summary', '') or ''
    facts = load_facts(raw)
    for field in (facts or {}).get('fields', []):
        if field.get('name') in {'企业名称', '单位名称', '投标人名称', '供应商名称', '签约主体','公司名称','企业全称','主体名称'} or (field.get('name')=='名称' and (facts or {}).get('role')=='company'):
            names.append(str(field.get('value') or '').strip())
    if raw.startswith(PERF_META_PREFIX):
        try:
            payload = json.loads(raw[len(PERF_META_PREFIX):])
        except json.JSONDecodeError:
            payload = {}
        if isinstance(payload, dict):
            names.append(str(payload.get('bidderName') or '').strip())
    names = [name for name in names if name]
    if names and any(not same_company(name, names[0]) for name in names):
        return '__conflicting_company__'
    return names[0] if names else ''
