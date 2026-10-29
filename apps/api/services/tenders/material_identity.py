"""Exact material-name aliases and file-content identity for scan placement."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path


def material_key_aliases(items, *, preferred_keys=()):
    preferred = set(preferred_keys)
    groups = {}
    by_key = {item.key.strip(): item for item in items if item.key.strip()}
    for key, item in by_key.items():
        title = re.sub(r"\s+", "", item.title or "").casefold()
        # Empty labels provide no evidence that two material keys are aliases.
        groups.setdefault(("title", title) if title else ("key", key), []).append(key)
    aliases = {}
    for keys in groups.values():
        canonical = next((key for key in keys if key in preferred), keys[0])
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
