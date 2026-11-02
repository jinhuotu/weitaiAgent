"""Company identity is input data, never a deployment-wide bidder constant."""
import json
import re

COMPANY_RE = re.compile(r'[\u4e00-\u9fffA-Za-z0-9·（）()]{2,60}(?:有限责任公司|股份有限公司|有限公司|事务所|合作社|研究院)')
BIDDER_RE = re.compile(r'(?:投标人|响应人|供应商|承包人|乙方|卖方)(?:名称|全称)?\s*[:：]\s*([^\n；;。]{2,100})')


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
