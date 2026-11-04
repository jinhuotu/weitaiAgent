"""Exact material-name aliases and file-content identity for scan placement."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path


def canonical_material_key(item):
    """Use evidence in the material name, not opaque generated key prefixes.

    Exact default names and narrow generic variants identify the same material.
    Named people/companies, reporting periods and distinct certificates remain
    separate; sharing a broad material category alone is not equivalence.
    """
    from api.services.tenders.placeholders import DEFAULT_SLOTS

    def normalized(title):
        title = re.sub(r'^(?:附[：:]|此处粘贴|在此粘贴)\s*', '', (title or '').strip())
        title = re.sub(r'(?:的)?(?:复印件或扫描件|复印件|扫描件)', '', title)
        title = re.sub(r'[（(](正本|副本)[）)]', r'\1', title)
        title = re.sub(r'[（(]加盖(?:单位)?公章[）)]$', '', title)
        return re.sub(r'\s+', '', title).casefold().strip('：:，,。；;')

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
        'bank_permit': r'(?:基本账户)?开户许可证(?:正本|副本)?',
        'credit': r'(?:企业)?信用(?:查询|报告|截图)(?:截图|打印件)?',
    }
    return next((key for key, pattern in variants.items() if re.fullmatch(pattern, title)), '')


def material_key_aliases(items, *, preferred_keys=()):
    preferred = set(preferred_keys)
    groups = {}
    by_key = {item.key.strip(): item for item in items if item.key.strip()}
    for key, item in by_key.items():
        standard = canonical_material_key(item)
        title = re.sub(r"\s+", "", item.title or "").casefold()
        # Empty labels provide no evidence that two material keys are aliases.
        group = ('standard', standard) if standard else (("title", title) if title else ("key", key))
        groups.setdefault(group, []).append(key)
    aliases = {}
    for group, keys in groups.items():
        canonical = group[1] if group[0] == 'standard' else next((key for key in keys if key in preferred), keys[0])
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
