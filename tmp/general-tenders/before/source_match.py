"""Conservative project/category checks before external bid evidence is adopted."""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from api.services.tenders.schema import BidBrief

_DOMAINS = {
    '医疗': ('医疗', '医用', '救护车', '急救', '监护仪', '呼吸机', '病床', '手术器械'),
    '充电设施': ('充电桩', '充电站', '充电枪', '充电板', '直流桩', '交流桩', '充电设施'),
    '土建道路': ('土方', '路基', '路面', '道路施工', '建筑施工', '土建', '市政工程',
             '桥梁', '公路工程', '公路施工', '施工组织设计', '工程里程', '施工里程',
             '道路里程', '施工年份', '里程桩号', '钢筋工程'),
    '信息系统': ('软件开发', '信息系统', '服务器采购', '网络设备采购'),
}
_GENERIC = re.compile(
    r'项目名称|车辆采购|采购项目|投标文件|响应文件|招标文件|技术资料|技术方案|报价清单|'
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


def _domains(text: str) -> set[str]:
    found = set()
    for label, words in _DOMAINS.items():
        for word in words:
            # Negative scope statements are not evidence of a foreign product.
            cleaned = re.sub(rf'(?:不含|不涉及|无需|不包含|非){re.escape(word)}', '', text or '')
            if word in cleaned:
                found.add(label)
                break
    return found


def match_source(
    brief: BidBrief, *, title: str = '', headers: Iterable[str] = (),
    rows: Iterable[tuple[str, str]] = (), text: str = '', source_name: str = '',
    projects: Iterable[str] = (),
) -> SourceMatch:
    """Compare only against project facts, never against already attached source data."""
    context = '\n'.join((brief.projectName or '', brief.bidContent or ''))
    expected = _domains(context)
    expected_name = _identity(brief.projectName)
    if _PLACEHOLDER.search(brief.projectName or ''):
        expected_name = ''
    header = ' '.join(headers)
    row_list = list(rows)
    positive = False
    # Check all rows, including a single foreign line hidden in a matching sheet.
    for index, (name, spec) in enumerate(row_list, 1):
        foreign = _domains(name + '\n' + spec) - expected
        if expected and foreign:
            reason = '、'.join(sorted(foreign))
            return SourceMatch('unmatched', f'第 {index} 行品名/规格含无关类别：{reason}')
        if expected == {'医疗'}:
            if (re.search(r'施工|工程里程|桩号', name)
                    or (re.fullmatch(r'(?:19|20)\d{2}年(?:度)?', name.strip())
                        and re.search(r'公里|里程|\bkm\b', spec + header, re.I))):
                if not _domains(name) & expected:
                    return SourceMatch('unmatched', f'第 {index} 行是施工/里程条目，非本次医疗品目')
        positive = positive or bool(expected & _domains(name + '\n' + spec))
    source_domains = _domains(title + '\n' + header + '\n' + text + '\n' + source_name)
    foreign = source_domains - expected
    if expected and foreign:
        reason = '、'.join(sorted(foreign))
        return SourceMatch('unmatched', '清单标题/表头或资料正文含无关类别：' + reason)
    positive = positive or bool(expected & source_domains)
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
