"""知识库检索：向量召回 + 中文短语关键词重排。"""

from __future__ import annotations

import re
from typing import Any

_WS_RE = re.compile(r"\s+")
_ASCII_TERM_RE = re.compile(r"[A-Za-z][A-Za-z0-9\-.]{1,}|\d+(?:\.\d+)?")
_STOP = frozenset(
    {
        "什么",
        "怎么",
        "如何",
        "是否",
        "可以",
        "一下",
        "这个",
        "那个",
        "请问",
        "帮我",
        "请你",
        "谢谢",
        "多少",
        "哪个",
        "哪些",
        "一下",
        "相关",
        "内容",
        "问题",
        "告诉",
        "介绍",
    }
)


def compact_search_query(query: str) -> str:
    """去掉寒暄前缀，过长时取尾部，减少对话句稀释向量。"""
    q = (query or "").strip()
    q = re.sub(r"^(请你|请您|麻烦你|麻烦|帮我|请问)[:：,，\s]*", "", q)
    if len(q) > 500:
        q = q[-500:]
    return q.strip() or (query or "").strip()


_SPLIT_RE = re.compile(r"[的是了吗呢啊吧和及与或，。？?、；;：:\s]+")
_DATETIME_RE = re.compile(
    r"\d{4}[-/]\d{1,2}[-/]\d{1,2}[ T]\d{1,2}:\d{2}(?::\d{2})?"
)


def extract_lookup_needles(query: str) -> list[str]:
    """抽出完整时间戳，供字面召回。只用最长形式，避免先命中当天前几行。"""
    found: list[str] = []
    seen: set[str] = set()
    for match in _DATETIME_RE.finditer(query or ""):
        raw = match.group(0).strip()
        variants = (
            raw,
            raw.replace("-", "/"),
            raw.replace("/", "-"),
            re.sub(r"\s+", " ", raw),
        )
        for item in variants:
            key = item.lower()
            if not item or key in seen:
                continue
            seen.add(key)
            found.append(item)
    found.sort(key=len, reverse=True)
    return found[:8]


def extract_query_phrases(query: str) -> list[str]:
    """取查询里的中文短语与英文/数字，避免整句 2 字滑窗造成噪声。"""
    compact = _WS_RE.sub("", query or "")
    phrases: list[str] = []
    seen: set[str] = set()

    def _add(term: str) -> None:
        key = term.lower()
        if not term or key in seen or term in _STOP or len(term) < 2:
            return
        seen.add(key)
        phrases.append(term)

    masked = query or ""
    for needle in extract_lookup_needles(query):
        _add(needle)
        masked = masked.replace(needle, " ")
    compact_masked = _WS_RE.sub("", masked)

    for part in _SPLIT_RE.split(compact_masked or compact):
        if not part:
            continue
        _add(part)
        if len(part) > 8:
            for n in (4, 3):
                for i in range(0, len(part) - n + 1):
                    _add(part[i : i + n])
        elif 4 <= len(part) <= 8:
            for n in (4, 3):
                if len(part) >= n:
                    _add(part[:n])
                    _add(part[-n:])
    for term in _ASCII_TERM_RE.findall(masked):
        _add(term)
    return phrases


def keyword_overlap_score(query: str, content: str, name: str = "") -> float:
    """短语命中加权分，范围 [0, 1]。长专名权重大于短词。"""
    hay = f"{name}\n{content}"
    compact_h = _WS_RE.sub("", hay)
    compact_q = _WS_RE.sub("", query or "")
    if not compact_q or not compact_h:
        return 0.0
    if compact_q in compact_h:
        return 1.0
    for needle in extract_lookup_needles(query):
        if needle and needle in hay:
            return 1.0

    phrases = extract_query_phrases(query)
    if not phrases:
        return 0.0

    hit = 0.0
    total = 0.0
    hay_l = compact_h.lower()
    for p in phrases:
        weight = min(5.0, max(1.0, len(p) / 2.0))
        total += weight
        needle = _WS_RE.sub("", p)
        if needle and (needle in compact_h or needle.lower() in hay_l):
            hit += weight
    return min(1.0, hit / total) if total else 0.0


def hybrid_rerank(
    query: str,
    hits: list[dict[str, Any]],
    *,
    top_k: int,
    keyword_weight: float,
) -> list[dict[str, Any]]:
    """对向量召回结果做关键词加权重排。"""
    w = max(0.0, min(1.0, float(keyword_weight)))
    ranked: list[dict[str, Any]] = []
    for h in hits:
        content = str(h.get("content") or "")
        name = str(h.get("name") or "")
        vec = float(h.get("score") or 0.0)
        kw = keyword_overlap_score(query, content, name)
        final = (1.0 - w) * vec + w * kw
        item = dict(h)
        item["vector_score"] = vec
        item["keyword_score"] = round(kw, 4)
        item["score"] = round(final, 6)
        ranked.append(item)
    ranked.sort(key=lambda x: float(x.get("score") or 0.0), reverse=True)
    return ranked[: max(1, top_k)]


def filter_weak_hits(
    hits: list[dict[str, Any]],
    *,
    floor: float,
    keep_ratio: float,
) -> list[dict[str, Any]]:
    """丢掉相对 top1 过弱的片段，减少无关块进入 prompt。"""
    if not hits:
        return hits
    best = float(hits[0].get("score") or 0.0)
    threshold = max(floor, best * max(0.0, min(1.0, keep_ratio)))
    kept = [h for h in hits if float(h.get("score") or 0.0) >= threshold]
    return kept or hits[:1]
