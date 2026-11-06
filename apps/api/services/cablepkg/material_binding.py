"""Narrow migrations for cable bid material classification and format ownership.

Operate on caller-owned models without importing legacy tender modules. Unknown
materials, distinct certificate subjects and ambiguous sections stay unchanged.
"""
import hashlib
import re

from .form_reconcile import title_kind

_NON_MATERIAL_FIELDS = {'投标有效期', '投标有效期要求', '保险期间', '保险期限', '交货期限', '交货期', '质保期'}
_NAME_PARTS = (
    ('cable_name_change_statement', '企业名称变更情况说明', 'statement'),
    ('cable_name_change_registry', '市场监督管理部门名称变更证明', 'attachment'),
    ('cable_name_change_qualification', '资格资质变更证明', 'attachment'),
)


def compact(value):
    return re.sub(r'\s+', '', value or '')


def is_field_requirement(title):
    return compact(title).strip('：:') in _NON_MATERIAL_FIELDS


def is_name_change_bundle(title):
    title = compact(title)
    return bool('名称变更' in title and ('原因说明' in title or '自述' in title)
                and ('名称变更证明' in title or '资格资质变更证明' in title))


def _family(title, key=''):
    form = title_kind(title)
    if form:
        return 'form:' + form
    if is_name_change_bundle(title):
        return 'name-change-bundle'
    if key == 'license' or ('营业执照' in title and not re.search(r'变更|免交|无需', title)):
        return 'license'
    return ''


def format_section(outline, family, previous=''):
    candidates = []
    for item in outline:
        if getattr(item, 'skipped', False):
            continue
        title = getattr(item, 'rawTitle', '') or item.title
        if '目录' in title or '上传端口' in title:
            continue
        if family == 'license':
            accepted = '营业执照' in title and not re.search(r'变更|说明|免交', title)
            score = 2 if '扫描件' in title else 1
        elif family == 'name-change-bundle':
            accepted = is_name_change_bundle(title)
            score = 1
        else:
            accepted = family == 'form:' + str(title_kind(title))
            score = 3 if re.search(r'[（(]格式[）)]', title) else 1
        if accepted:
            candidates.append((score, title))
    if not candidates:
        return previous
    score = max(s for s, _ in candidates)
    titles = list(dict.fromkeys(t for s, t in candidates if s == score))
    return titles[0] if len(titles) == 1 else previous


def normalize_demands(brief, demands):
    """Return normalized demands and migrate only their proven legacy aliases.

    Name-change proofs remain separate from the company's own statement and
    retain the source's conditional wording; no change/no-proof is inferred.
    """
    rows = []
    aliases = {}
    families = {}
    for demand in demands:
        if is_field_requirement(demand.title):
            aliases[demand.slotKey] = []
            continue
        family = _family(demand.title, demand.slotKey)
        target = format_section(brief.outlineItems, family, demand.section) if family else demand.section
        if family == 'name-change-bundle':
            note = '企业名称变更证明按原文“如有”要求保留，是否适用及原件须人工确认；企业说明不替代官方证明。'
            if note not in brief.materialIntentNotes:
                brief.materialIntentNotes.append(note)
            children = []
            evidence = compact(demand.title + demand.text)
            for key, title, mode in _NAME_PARTS:
                if key.endswith('_qualification') and not re.search(r'(?:资格)?资质变更证明', evidence):
                    continue
                if key.endswith('_registry') and '名称变更证明' not in evidence:
                    continue
                child = demand.model_copy(update={
                    'id': 'demand_' + hashlib.sha256((demand.id + ':' + key).encode()).hexdigest()[:20],
                    'slotKey': key, 'title': title, 'mode': mode, 'section': target,
                    'volume': 'business', 'condition': demand.condition or 'name_change',
                    'reviewRequired': True, 'outcomes': {}, 'placements': {},
                })
                children.append(child)
                rows.append(child)
            aliases[demand.slotKey] = [c.slotKey for c in children]
            families[family] = children
        else:
            update = {'section': target}
            if family.startswith('form:'):
                update['mode'] = 'form'
            rows.append(demand.model_copy(update=update))
            if family:
                families[family] = [rows[-1]]
    # Only the explicitly recognized bundle is collapsed across legacy keys.
    # All source clauses remain in child.text for inspection and applicability.
    unique = {}
    for demand in rows:
        if demand.slotKey.startswith('cable_name_change_'):
            token = (demand.slotKey, compact(demand.section), demand.condition)
            previous = unique.get(token)
            if previous is not None:
                evidence = list(dict.fromkeys([previous.text, demand.text]))
                unique[token] = previous.model_copy(update={'text': '\n'.join(evidence)})
                continue
        else:
            token = ('original', demand.id)
        unique[token] = demand
    normalized = list(unique.values())
    new_slots = []
    for slot in brief.extraPlaceholders:
        if is_field_requirement(slot.title):
            aliases[slot.key] = []
            continue
        family = _family(slot.title, slot.key)
        if family == 'name-change-bundle' and family in families:
            aliases[slot.key] = [c.slotKey for c in families[family]]
            continue
        if slot.key in aliases and not aliases[slot.key]:
            continue
        target = format_section(brief.outlineItems, family, slot.attachToSection) if family else slot.attachToSection
        new_slots.append(slot.model_copy(update={'attachToSection': target}))
    brief.extraPlaceholders = new_slots
    for field in ('requiredSlotKeys', 'includeSlotKeys'):
        migrated = []
        for key in getattr(brief, field):
            migrated.extend(aliases.get(key, [key]))
        setattr(brief, field, list(dict.fromkeys(migrated)))
    return normalized
