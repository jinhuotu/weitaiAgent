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


_LOOK_RE = re.compile(
    r"^(查询一下|查看一下|看一下|查一下|找一下|看看|查查|看下)[:：,，\s]*"
)
_PREFIX_RE = re.compile(r"^(请你|请您|麻烦你|麻烦您|麻烦|帮我|请问)[:：,，\s]*")


def compact_search_query(query: str) -> str:
    """去掉寒暄前缀，过长时取尾部，减少对话句稀释向量。"""
    q = (query or "").strip()
    for _ in range(4):
        nxt = _PREFIX_RE.sub("", q, count=1).strip()
        if nxt == q:
            break
        q = nxt
    q = _LOOK_RE.sub("", q, count=1).strip()
    q = re.sub(r"^(我的|我们的)[:：,，\s]*", "", q).strip()
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
    name_compact = _WS_RE.sub("", name or "")
    for p in phrases:
        weight = min(5.0, max(1.0, len(p) / 2.0))
        total += weight
        needle = _WS_RE.sub("", p)
        if needle and (needle in compact_h or needle.lower() in hay_l):
            hit += weight
            if len(needle) >= 3 and needle in name_compact:
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


_GENERIC_PHRASE = frozenset(
    {
        "查看一下",
        "看一下",
        "查一下",
        "找一下",
        "帮我查看",
        "查看一",
        "看一下我",
        "一下我",
        "我的",
    }
)


def topic_keys(query: str, hits: list[dict[str, Any]]) -> list[str]:
    """查询里能对上资料名称的短语，用来丢掉同库里语义相近但主题不同的件。"""
    phrases = [
        p
        for p in extract_query_phrases(query)
        if len(p) >= 3 and p not in _STOP and p not in _GENERIC_PHRASE
    ]
    if not phrases or not hits:
        return []
    names = [_WS_RE.sub("", str(h.get("name") or "")) for h in hits]
    keys = [p for p in phrases if any(p in n for n in names)]
    if keys:
        return keys
    blobs = [
        _WS_RE.sub("", f"{h.get('name') or ''}{h.get('content') or ''}") for h in hits
    ]
    return [p for p in phrases if any(p in b for b in blobs)]


def restrict_topic_hits(
    query: str, hits: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    keys = topic_keys(query, hits)
    if not keys:
        return hits
    kept: list[dict[str, Any]] = []
    for h in hits:
        blob = _WS_RE.sub("", f"{h.get('name') or ''}{h.get('content') or ''}")
        if any(k in blob for k in keys):
            kept.append(h)
    return kept or hits


def _slot_key(tags: object) -> str:
    if not isinstance(tags, list):
        return ""
    for raw in tags:
        text = str(raw or "").strip()
        if text.startswith("slot:"):
            return text[5:].strip()
    return ""


def query_slot_keys(query: str) -> set[str]:
    from api.services.tenders.match import _kind_hits

    q = compact_search_query(query).replace("的", "")
    return _kind_hits(q)


def restrict_slot_hits(
    query: str, hits: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    wanted = query_slot_keys(query)
    if not wanted:
        return hits
    kept: list[dict[str, Any]] = []
    for h in hits:
        slot = _slot_key(h.get("tags"))
        if slot and slot in wanted:
            kept.append(h)
    return kept or hits
