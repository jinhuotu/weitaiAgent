"""按邀请书技术章起草可编辑草稿，缺事实标待补充。"""

from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from api.services.tenders.categories import PENDING_DRAFT, PENDING_FACT, is_tech_chapter_kind
from api.services.tenders.outline import ensure_tech_chapters, hydrate_tech_excerpts
from api.services.tenders.schema import BidBrief, DeviationLine, OutlineItem

logger = logging.getLogger("api.tenders.tech_draft")

TECH_BID_TAG = "tech_bid"
_PENDING_RESP = "【待响应】"

_DRAFT_SYSTEM = """【四源优先级：邀请书原文 > 用户本次输入(slots) > 投标资料库最新数据 > 提示词模板结构 > 历史标书写法】
你是投标技术标起草助理。根据给定的招标原文、评分点和本标已有事实，写一章响应草稿。
硬性规则：
1. 只使用用户给出的事实。数字、工期、数量、图纸、人员名单不在事实里就写【待补充】，禁止编造。
2. 历史片段只可参考写法（不得照抄其中项目名、数量、甲方名、日期、金额）；证照号、业绩合同号、授权日期必须原样引用投标资料库最新件；slots缺失写【待确认】，不得照抄其中的项目名、数量或甲方名称。
3. 不要写投标人全称、徽标、人员真名（暗标）。
4. 只输出本章正文，不要标题、不要 Markdown 代码块。"""


def quote_facts(brief: BidBrief) -> str:
    rows: list[str] = []
    for line in brief.quoteLines or []:
        name = (line.name or "").strip()
        spec = (line.spec or "").strip()
        if not name and not spec:
            continue
        extra = []
        if line.unit:
            extra.append(str(line.unit))
        if line.qty:
            extra.append(str(line.qty))
        tail = " ".join(extra)
        rows.append(" ".join(p for p in (name, spec, tail) if p))
    return "\n".join(rows[:40])


def scores_for(item: OutlineItem, brief: BidBrief) -> list[str]:
    title = compact(item.title)
    out: list[str] = []
    for req in brief.bidRequirements or []:
        if (req.category or "").strip() != "scoring":
            continue
        blob = compact(f"{req.text}{req.targetTitle}{req.targetKind}")
        if req.targetKind in {"tech_plan", "tech_chapter"} or _overlap(title, blob):
            text = (req.text or "").strip()
            if text and text not in out:
                out.append(text)
    return out[:8]


def compact(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _overlap(a: str, b: str) -> bool:
    if not a or not b:
        return False
    if a in b or b in a:
        return True
    return any(len(p) >= 4 and p in b for p in (a[i : i + 6] for i in range(0, max(0, len(a) - 3))))


def fallback_draft(
    item: OutlineItem,
    *,
    facts: str = "",
    scores: list[str] | None = None,
    past: list[str] | None = None,
) -> str:
    excerpt = (item.inviteExcerpt or item.body or "").strip()
    bits: list[str] = []
    if excerpt:
        bits.append("对照招标要求：\n" + excerpt[:800])
    if scores:
        bits.append("评分点：\n" + "\n".join(f"- {s}" for s in scores[:8]))
    if facts.strip():
        bits.append("本标已有参数：\n" + facts[:800])
    else:
        bits.append(f"{PENDING_FACT} 本章尚无报价/产品参数，请人工补写，不要编造数量。")
    if past:
        bits.append("历史写法仅供参考，不得照抄项目名和数量。")
    if not excerpt and not facts.strip():
        return f"{_PENDING_RESP} 缺少招标原文与事实，请人工编写本章。"
    return "\n\n".join(bits)


def _match_fact(requirement: str, facts: str) -> str:
    req = compact(requirement)
    if not req or not facts:
        return ""
    for line in facts.splitlines():
        blob = compact(line)
        if len(blob) < 2:
            continue
        if blob in req or req in blob:
            return line.strip()
        if any(len(p) >= 4 and p in blob for p in (req[i : i + 4] for i in range(0, max(0, len(req) - 3)))):
            return line.strip()
    return ""


def draft_deviations(brief: BidBrief, facts: str = "") -> BidBrief:
    rows = list(brief.deviationLines or [])
    if not rows:
        return brief
    out: list[DeviationLine] = []
    for i, row in enumerate(rows, start=1):
        req = (row.requirement or "").strip()
        if not req:
            continue
        resp = (row.response or "").strip()
        dev = (row.deviation or "").strip()
        hit = _match_fact(req, facts)
        if hit:
            if not resp or resp == req or resp.startswith("【"):
                resp = f"响应：{hit}"
            if not dev:
                dev = "待确认"
        else:
            if not resp or resp == req:
                resp = _PENDING_RESP
            if not dev or (resp == _PENDING_RESP and dev == "无偏差"):
                dev = "待确认"
        out.append(
            DeviationLine(
                seq=(row.seq or str(i)).strip() or str(i),
                requirement=req,
                response=resp,
                deviation=dev,
            )
        )
    brief.deviationLines = out
    return brief


def apply_chapter_draft(item: OutlineItem, text: str) -> OutlineItem:
    body = (text or "").strip() or PENDING_DRAFT
    excerpt = (item.inviteExcerpt or "").strip() or (item.body or "").strip()
    return item.model_copy(
        update={
            "draftBody": body,
            "draftStatus": "drafted",
            "inviteExcerpt": excerpt,
        }
    )


async def retrieve_past_fragments(
    db: AsyncSession | None,
    item: OutlineItem,
    kb_ids: list[str] | None = None,
    *,
    search=None,
) -> list[str]:
    if search is None:
        if db is None:
            return []
        from api.services.knowledge.ingest import search_chunks

        search = search_chunks
    q = f"{item.title or ''} {(item.inviteExcerpt or item.body or '')[:80]}".strip()
    if not q:
        return []
    try:
        hits = await search(db, query=q, top_k=3, kb_ids=kb_ids or None)
    except Exception as exc:
        logger.warning("tech draft retrieve skipped: %s", exc)
        return []
    texts: list[str] = []
    tagged: list[str] = []
    for hit in hits or []:
        if not isinstance(hit, dict):
            continue
        text = str(hit.get("content") or hit.get("text") or "").strip()
        if not text:
            continue
        tags = hit.get("tags") or []
        if isinstance(tags, str):
            tags = [tags]
        chunk = text[:600]
        if TECH_BID_TAG in {str(t) for t in tags}:
            tagged.append(chunk)
        else:
            texts.append(chunk)
    return (tagged or texts)[:3]


def _user_prompt(item: OutlineItem, facts: str, scores: list[str], past: list[str]) -> str:
    parts = [
        f"本章标题：{item.title or '技术方案'}",
        f"招标原文：\n{(item.inviteExcerpt or item.body or '').strip() or '（无）'}",
    ]
    if scores:
        parts.append("评分点：\n" + "\n".join(f"- {s}" for s in scores))
    parts.append(f"本标已有事实：\n{facts.strip() or '（无）'}")
    if past:
        parts.append("历史技术标片段（参考写法）：\n" + "\n---\n".join(past))
    return "\n\n".join(parts)


async def write_chapter(
    item: OutlineItem,
    *,
    facts: str,
    scores: list[str],
    past: list[str],
    client: Any | None = None,
) -> str:
    if client is None:
        return fallback_draft(item, facts=facts, scores=scores, past=past)
    try:
        raw = await client.complete(
            [
                {"role": "system", "content": _DRAFT_SYSTEM},
                {"role": "user", "content": _user_prompt(item, facts, scores, past)},
            ],
            mode="fast",
        )
        text = (raw or "").strip()
        if text:
            return text
    except Exception as exc:
        logger.warning("tech draft llm skipped: %s", exc)
    return fallback_draft(item, facts=facts, scores=scores, past=past)


async def draft_tech(
    brief: BidBrief,
    *,
    db: AsyncSession | None = None,
    kb_ids: list[str] | None = None,
    item_id: str | None = None,
    client: Any | None = None,
    search=None,
) -> tuple[BidBrief, list[str]]:
    notes: list[str] = []
    items = hydrate_tech_excerpts(list(brief.outlineItems or []))
    if not item_id:
        items = ensure_tech_chapters(items, brief)
    brief = brief.model_copy(update={"outlineItems": items})
    facts = quote_facts(brief)
    done = 0
    out: list[OutlineItem] = []
    for item in brief.outlineItems:
        hit = (
            (item.id or "") == item_id
            if item_id
            else (not item.skipped and is_tech_chapter_kind(item.kind))
        )
        if item_id:
            hit = hit and is_tech_chapter_kind(item.kind)
        if not hit:
            out.append(item)
            continue
        past = await retrieve_past_fragments(db, item, kb_ids, search=search)
        if past:
            notes.append(f"{item.title}引用了 {len(past)} 段历史写法")
        text = await write_chapter(
            item, facts=facts, scores=scores_for(item, brief), past=past, client=client
        )
        out.append(apply_chapter_draft(item, text))
        done += 1
    brief = brief.model_copy(update={"outlineItems": out})
    if not item_id:
        brief = draft_deviations(brief, facts)
        notes.append(f"已起草 {done} 章技术标草稿")
    elif done:
        title = next((i.title for i in out if (i.id or "") == item_id), "")
        notes.append(f"已重写「{title}」")
    return brief, notes
