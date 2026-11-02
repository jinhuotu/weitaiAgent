"""Conservative project/category checks before external bid evidence is adopted."""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from api.services.tenders.schema import BidBrief

_GENERIC = re.compile(
    r'项目名称|采购项目|投标文件|响应文件|招标文件|技术资料|技术方案|报价清单|'
    r'分项报价|工程量清单|报价表|清单|报价|采购|项目|文件|方案|定稿|正本|二次|'
    r'xlsx|xlsm|csv|docx|pdf', re.I,
)
_ORG = re.compile(r'[\u4e00-\u9fffA-Za-z0-9]{1,35}(?:医院|公司|大学|学校|园区|小区|卫生院)')
_NAMED = re.compile(r'项目|医院|大学|学校|园区|小区|标段|卫生院')
_PLACEHOLDER = re.compile(r'测试|示例|本项目|某项目|未命名|要求型|演示')


@dataclass(frozen=True)
class SourceMatch:
    status: str  # matched / unmatched / unverified
    reason: str


def _compact(text: str) -> str:
    return re.sub(r'[^\u4e00-\u9fffA-Za-z0-9]+', '', text or '').lower()


def _identity(text: str) -> str:
    return _compact(_GENERIC.sub('', text or ''))


def _related(left: str, right: str) -> bool:
    return bool(left and right and (left in right or right in left))


def _subject_terms(text: str) -> set[str]:
    # Subject phrases come from this bid. Keep full noun phrases: a shared
    # two-character prefix is not proof that two different products are the same.
    text = _ORG.sub(' ', text or '')
    text = _GENERIC.sub(' ', text)
    text = re.sub(r'设备|系统|服务|工程|要求|名称|资料|证明|本次|提供|符合|包括|以及|相关|技术|采购|数量|单位|配套|附属|及其|[与及和]', ' ', text)
    tokens = {w.casefold() for w in re.findall(r'[A-Za-z][A-Za-z0-9-]{1,}', text)}
    tokens.update(re.findall(r'[\u4e00-\u9fff]{2,}', text))
    return tokens


def _term_overlap(left, right):
    for a in left:
        for b in right:
            if min(len(a), len(b)) < 2:
                continue
            if _related(a, b):
                return True
            # Different modifiers may describe the same noun (e.g. a product's
            # type versus its intended use). Require a substantial shared noun
            # suffix; a two-character/common prefix is insufficient evidence.
            for width in range(min(len(a), len(b)), 2, -1):
                if a[-width:] == b[-width:] and width * 2 >= max(len(a), len(b)):
                    return True
    return False


def match_source(
    brief: BidBrief, *, title: str = '', headers: Iterable[str] = (),
    rows: Iterable[tuple[str, str]] = (), text: str = '', source_name: str = '',
    projects: Iterable[str] = (),
) -> SourceMatch:
    """Compare only against project facts, never against already attached source data."""
    context = '\n'.join((brief.projectName or '', brief.bidContent or ''))
    expected = _subject_terms(context + '\n' + '\n'.join(r.text for r in brief.bidRequirements))
    if _PLACEHOLDER.search(brief.projectName or ''):
        expected = _subject_terms((brief.bidContent or '') + '\n' + '\n'.join(r.text for r in brief.bidRequirements))
    expected_name = _identity(brief.projectName)
    if _PLACEHOLDER.search(brief.projectName or ''):
        expected_name = ''
    header = ' '.join(headers)
    row_list = list(rows)
    positive = False
    # A matching project title cannot legitimize a foreign line. Generic totals,
    # tax/transport/installation fees are structural rows, not product evidence.
    for index, (name, spec) in enumerate(row_list, 1):
        if re.fullmatch(r'.{0,8}(?:合计|小计|税额|税金|运费|运输费|安装费|调试费|管理费)', name.strip()):
            continue
        terms = _subject_terms(name + '\n' + spec)
        overlap = _term_overlap(expected, terms)
        # Explicitly listed additional entities need their own tender evidence;
        # a matching main item cannot hide unrelated appended equipment.
        for extra in re.findall(r'(?:包含|包括|配置|配备|含)([^\d，。；;]{2,30}?)\s*\d+\s*(?:台|套|组|辆|件)', spec):
            if not re.search(r'(?:不含|无需|不包含)' + re.escape(extra), spec):
                extra_terms = _subject_terms(extra)
                if expected and extra_terms and not _term_overlap(expected, extra_terms):
                    return SourceMatch('unmatched', f'第 {index} 行附带数量的额外品目无招标依据')
        if (re.search(r'(?:19|20)\d{2}年.*?\d+\s*(?:公里|km)', spec, re.I)
                and not re.search(r'生产|出厂|制造|续航|额定|性能', spec)):
            return SourceMatch('unmatched', f'第 {index} 行混入年度与累计工程量，需核对来源')
        if expected and terms and not overlap:
            return SourceMatch('unmatched', f'第 {index} 行品名/规格在本次采购要求中无关联依据')
        positive = positive or bool(overlap)
    source_terms = _subject_terms(title + '\n' + header + '\n' + text + '\n' + source_name)
    positive = positive or _term_overlap(expected, source_terms)
    identities = list(projects)
    identities.extend(re.findall(r'(?:项目名称|工程名称)\s*[:：]\s*([^\n|；;]{2,120})',
                                 title + '\n' + text))
    for candidate in (title, source_name.rsplit('.', 1)[0]):
        if _NAMED.search(candidate or '') and not _PLACEHOLDER.search(candidate or ''):
            identities.append(candidate)
    for candidate in dict.fromkeys(identities):
        actual = _identity(candidate)
        if not expected_name or not actual:
            continue
        if _related(expected_name, actual):
            positive = True
            continue
        # Accept project-name abbreviations with the same organization and subject.
        orgs = [_compact(word) for word in _ORG.findall(candidate)]
        own_orgs = [_compact(word) for word in _ORG.findall(brief.projectName)]
        if orgs and own_orgs and not any(_related(a, b) for a in orgs for b in own_orgs):
            return SourceMatch('unmatched', '来源项目/单位名称与本次项目不同')
        if orgs and own_orgs and _term_overlap(expected, _subject_terms(candidate)):
            positive = True
            continue
        return SourceMatch('unmatched', '来源项目名称与本次项目不同或无法确认属于同一项目')
    # Shared specific subject words also support non-domain projects and generic attachments.
    source_words = _identity(title + '\n' + text + '\n' + '\n'.join(n + s for n, s in row_list))
    title_key = _identity(title)
    positive = positive or (len(title_key) >= 3 and _related(expected_name, title_key))
    for raw in (brief.projectName, brief.bidContent):
        key = _identity(raw)
        if len(key) >= 3 and key in source_words:
            positive = True
    if positive:
        return SourceMatch('matched', '项目名称或采购品类相关，未发现跨项目冲突')
    if not context.strip():
        return SourceMatch('unverified', '项目信息尚未填写，无法确认来源归属')
    return SourceMatch('unverified', '缺少可核对的项目名称/品类依据，无法确认属于本标书')


def require_source_match(result: SourceMatch) -> SourceMatch:
    """External attachments need positive evidence before becoming bid facts."""
    if result.status == 'unverified':
        return SourceMatch('unmatched', result.reason)
    return result
