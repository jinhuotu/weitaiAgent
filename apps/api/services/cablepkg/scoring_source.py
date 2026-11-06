"""Source-preserving scoring units; no fixed chapter numbers or product names."""
from dataclasses import dataclass
import hashlib
import re

SCORING_SIGNAL = re.compile(r'评分|得分|扣分|加分|权重|分值|基准价|[+-]?\d+(?:\.\d+)?\s*分|score|weight', re.I)
POINTS = re.compile(r'[+-]?\d+(?:\.\d+)?\s*分|扣分|加分|权重|基准价|得分\s*[=＝]', re.I)
REFERENCE = re.compile(r'详见|参见|见[《「]|按.{0,25}(?:附表|附件|另册)|另行提供|另见')


@dataclass(frozen=True)
class Unit:
    id: str
    offset: int
    text: str
    kind: str
    section: str
    table_header: str
    candidate: bool

    def prompt(self):
        return {'id': self.id, 'text': self.text, 'kind': self.kind,
                'section': self.section, 'tableHeader': self.table_header}


def source_units(source):
    """Keep table rows and paragraphs intact, with exact character provenance.

    Input from existing file parsers retains DOCX table rows as pipe-separated
    lines and OCR/page markers. No OCR is repeated or silently fabricated here.
    """
    units = []
    section = ''
    header = ''
    offset = 0
    in_table = False
    for raw in source.splitlines(keepends=True):
        value = raw.strip()
        start = offset + len(raw) - len(raw.lstrip())
        offset += len(raw)
        if not value:
            continue
        table = '|' in value
        heading = (not table and len(value) <= 160 and (
            bool(re.match(r'^(?:#{1,6}\s|第.{1,12}[章节篇]|[一二三四五六七八九十]+[、.]|\d+(?:\.\d+)*[、.])', value))
            or bool(re.search(r'(?:评分|评审|评价|评标).{0,12}(?:标准|细则|办法|规则|表)[：:]?$', value))))
        if table and not in_table:
            header = value
        elif not table:
            header = ''
        if heading:
            section = value
        kind = 'heading' if heading else 'table_header' if table and not in_table and not POINTS.search(value) else 'table_row' if table else 'paragraph'
        candidate = bool(SCORING_SIGNAL.search(value) or (table and SCORING_SIGNAL.search(section + header)))
        digest = hashlib.sha256(f'{start}:{value}'.encode()).hexdigest()[:16]
        units.append(Unit('unit_' + digest, start, value, kind, section, header, candidate))
        in_table = table
    return units


def batches(source, units, size=10000, hard_limit=24000):
    """Pack complete units; context is metadata and never prepended to quotations."""
    current = []
    length = 0
    for unit in units:
        if len(unit.text) > hard_limit:
            if current:
                yield current
                current = []; length = 0
            yield [unit]
            continue
        if current and (length + len(unit.text) > size or len(current) >= 40):
            yield current
            current = []; length = 0
        current.append(unit); length += len(unit.text)
    if current:
        yield current


def condition_coverage(unit, rows):
    """Retain the complete rule cell/paragraph, not just its scoring numbers.

    Table label columns may be context, but proof requirements or notes following
    a scoring cell remain conditions. They cannot disappear behind complete=true.
    """
    required = unit.text
    if unit.kind == 'table_row':
        cursor = 0
        for cell in unit.text.split('|'):
            if POINTS.search(cell) or SCORING_SIGNAL.search(cell):
                required = unit.text[cursor:].strip()
                break
            cursor += len(cell) + 1
    start = unit.offset + unit.text.find(required)
    end = start + len(required)
    intervals = sorted((max(start, r['sourceOffset']), min(end, r['sourceOffset'] + len(r['requirement'])))
                       for r in rows if r['sourceOffset'] < end and r['sourceOffset'] + len(r['requirement']) > start)
    position = start
    for left, right in intervals:
        if left > position and unit.text[position-unit.offset:left-unit.offset].strip():
            return False
        position = max(position, right)
    return position >= end


def literal_coverage(unit, rows):
    return condition_coverage(unit, rows)


def classify_rule(quote):
    if re.search(r'权重|占比|weight', quote, re.I):
        return 'weight'
    if re.search(r'基准价|平均价|最低报价|最高报价|相对报价|价格部分得分|报价.{0,12}得分\s*[=＝]', quote):
        return 'relative_price'
    if re.search(r'综合评价|酌情|优良|较强|主观|由评委|评委.{0,12}打分', quote):
        return 'subjective'
    if re.search(r'扣\s*\d|扣分|不扣分|扣减', quote):
        return 'deduction'
    if len(re.findall(r'\d+(?:\.\d+)?\s*分', quote)) > 1:
        return 'tiered'
    if re.search(r'\d+(?:\.\d+)?\s*[-–~至]\s*\d+(?:\.\d+)?\s*分', quote):
        return 'range'
    if re.search(r'\d+(?:\.\d+)?\s*分', quote):
        return 'fixed'
    return 'unknown'
