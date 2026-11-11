"""Exact material-name aliases and file-content identity for scan placement."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path


def standard_material_key(item):
    """Storage category, distinct from the identity of a physical certificate.

    Categories may contain several companies, people or reporting periods;
    ownership and exact document selection remain downstream requirements.
    Unknown requests stay in memory and use a shared container only on upload.
    """
    from api.services.tenders.placeholders import DEFAULT_SLOTS
    from api.services.tenders.match import _kind_hits

    known = {slot.key for slot in DEFAULT_SLOTS} | {'qualification', 'safety', 'other_materials'}
    if item.key in known:
        return item.key
    if item.key == 'bank_guarantee':
        return 'bond'
    direct = canonical_material_key(item)
    if direct:
        return direct
    from api.services.tenders.match import normalize_material_title

    name = normalize_material_title(item.title)
    if not (_kind_hits(name) & known):
        name += ' ' + str(item.hint or '')
    if re.search(r'基本(?:存款)?账户|开户许可', name):
        return 'bank_permit'
    if re.search(r'投标保证金|(?:电子|银行)保函|保证金.*(?:回单|凭证|缴存)', name):
        return 'bond'
    if re.search(r'资质证书|企业资质', name) and not re.search(r'iso|体系', name, re.I):
        return 'qualification'
    hits = _kind_hits(name) & known
    return next(iter(hits)) if len(hits) == 1 else ''


def canonical_material_key(item):
    """Use evidence in the material name, not opaque generated key prefixes.

    Exact default names and narrow generic variants identify the same material.
    Named people/companies, reporting periods and distinct certificates remain
    separate; sharing a broad material category alone is not equivalence.
    """
    from api.services.tenders.placeholders import DEFAULT_SLOTS

    def normalized(title):
        from api.services.tenders.match import normalize_material_title

        title = re.sub(r'^(?:附[：:]|此处粘贴|在此粘贴)\s*', '', (title or '').strip())
        title = re.sub(r'(?:的)?(?:复印件或扫描件|复印件|扫描件)', '', title)
        title = re.sub(r'[（(](正本|副本)[）)]', r'\1', title)
        title = re.sub(r'[（(]加盖(?:单位)?公章[）)]$', '', title)
        title = re.sub(r'[（(]\s*[）)]', '', title)
        return normalize_material_title(title)

    title = normalized(item.title)
    defaults = {slot.key: slot for slot in DEFAULT_SLOTS}
    if item.key in defaults:
        return item.key
    for key, slot in defaults.items():
        if title and title == normalized(slot.title):
            return key
    variants = {
        'license': r'(?:本企业|本公司|本单位|企业|企业法人)?营业执照(?:正本|副本)?',
        'id_legal': r'(?:法定代表人|法人代表|法人)身份证(?:正反面|正面及反面)?',
        'id_agent': r'(?:授权代理人|委托代理人|授权委托人|被授权人|代理人)身份证(?:正反面|正面及反面)?',
        'bank_permit': r'(?:(?:基本账户)?开户许可证(?:正本|副本)?|基本存款账户(?:信息|证明)?|基本账户(?:信息|开户证明))',
        'bond': r'(?:(?:投标)?保证金(?:缴存回单|缴纳凭证|缴纳证明|电子保函凭证|回单|汇款凭证|凭证)?|银行保函|电子保函)(?:[（(][^）)]*[）)])?',
        'credit': r'(?:企业)?信用(?:查询|报告|截图)(?:截图|打印件)?',
    }
    return next((key for key, pattern in variants.items() if re.fullmatch(pattern, title)), '')


def material_key_aliases(items, *, preferred_keys=()):
    from api.services.tenders.match import normalize_material_title, material_sources, _kind_hits

    preferred = set(preferred_keys)
    groups = {}
    by_key = {item.key.strip(): item for item in items if item.key.strip()}
    for key, item in by_key.items():
        standard = canonical_material_key(item)
        title = normalize_material_title(item.title)
        # Empty labels provide no evidence that two material keys are aliases.
        group = ('standard', standard) if standard else (("title", title) if title else ("key", key))
        sources = material_sources(str(item.title or '') + ' ' + str(item.hint or ''))
        kinds = frozenset(_kind_hits(title) or _kind_hits(item.hint))
        groups.setdefault((group, sources, kinds), []).append(key)
    aliases = {}
    for (group, sources, kinds), keys in groups.items():
        canonical = group[1] if group[0] == 'standard' and not sources else next((key for key in keys if key in preferred), keys[0])
        aliases.update((key, canonical) for key in keys)
    return aliases


def file_identity(path: Path, cache: dict) -> str:
    resolved = path.resolve()
    if resolved not in cache:
        try:
            with path.open("rb") as stream:
                cache[resolved] = "sha256:" + hashlib.file_digest(stream, "sha256").hexdigest()
        except OSError:
            # Leave unreadable files for the existing renderer's error handling.
            cache[resolved] = "path:" + str(resolved)
    return cache[resolved]


def unique_material_files(files, *, cache=None):
    cache = {} if cache is None else cache
    result = []
    seen = set()
    for value in files:
        path = Path(value)
        identity = file_identity(path, cache)
        if identity not in seen:
            seen.add(identity)
            result.append(path)
    return result
