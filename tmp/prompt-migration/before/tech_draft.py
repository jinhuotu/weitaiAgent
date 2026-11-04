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
2. 历史片段只可参考写法（不得照抄其中项目名、数量、甲方名、日期、金额）；证照号、注册证号、合同编号、合同金额必须原样引用「可引用素材」，没有就写【待补充】；slots缺失写【待确认】。
3. 不要写投标人全称、徽标、人员真名（暗标）。
4. 只输出本章正文，不要标题、不要 Markdown 代码块。
5. 若给出评分点，正文逐条对应：每条先回应评分点原意，再写响应。
6. 若给出红线，不得写出会触发红线的承诺或相反表述；做不到写【待补充】。"""

_CITE_FIELDS = (
    "统一社会信用代码",
    "证书编号",
    "注册证号",
    "资质等级",
    "证书名称",
    "项目名称",
    "合同编号",
    "合同金额",
)


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


def tech_red_lines(brief: BidBrief) -> list[str]:
    out: list[str] = []
    for req in brief.bidRequirements or []:
        if (req.volume or "").strip() != "technical":
            continue
        if (req.strength or "").strip() != "mandatory":
            continue
        text = re.sub(r"\s+", " ", (req.text or "").strip())[:120]
        if len(text) < 4 or text in out:
            continue
        out.append(text)
        if len(out) >= 12:
            break
    return out


def _tags_of(hit: dict) -> set[str]:
    tags = hit.get("tags") or []
    if isinstance(tags, str):
        tags = [tags]
    return {str(t) for t in tags}


def _outcome_of(tags: set[str]) -> str:
    for tag in tags:
        if tag.startswith("outcome:"):
            return tag[len("outcome:") :]
    return ""


def _avoid_from(mark: dict, outcome: str) -> str:
    labels = [str(t).strip() for t in (mark.get("problemTags") or []) if str(t).strip()]
    note = str(mark.get("note") or "").strip()[:200]
    bits: list[str] = []
    if labels:
        bits.append("问题：" + "、".join(labels))
    if note:
        bits.append(note)
    if not bits and outcome == "lost":
        bits.append("未中标章节，不要沿用其缺项")
    return "；".join(bits)


def rank_past_hits(hits, verdicts: dict | None = None) -> tuple[list[str], list[str]]:
    held = verdicts or {}
    good: list[str] = []
    mid: list[str] = []
    other: list[str] = []
    avoid: list[str] = []
    saw_tag = False
    for hit in hits or []:
        if not isinstance(hit, dict):
            continue
        text = str(hit.get("content") or hit.get("text") or "").strip()
        if not text:
            continue
        tags = _tags_of(hit)
        if TECH_BID_TAG in tags:
            saw_tag = True
        doc_id = str(hit.get("doc_id") or "").strip()
        mark = held.get(doc_id) or {}
        verdict = str(mark.get("verdict") or "")
        outcome = _outcome_of(tags)
        if verdict == "bad" or (verdict != "good" and outcome == "lost"):
            line = _avoid_from(mark, outcome)
            if line and line not in avoid:
                avoid.append(line)
            continue
        chunk = text[:600]
        if verdict == "good" or outcome == "won":
            good.append(chunk)
        elif TECH_BID_TAG in tags:
            mid.append(chunk)
        else:
            other.append(chunk)
    pool = good + mid
    if not saw_tag:
        pool = pool + other
    samples: list[str] = []
    for chunk in pool:
        if chunk and chunk not in samples:
            samples.append(chunk)
        if len(samples) >= 3:
            break
    return samples, avoid[:6]


def scrub_fragment(text: str, brief: BidBrief | None) -> str:
    if not text or brief is None:
        return text or ""
    needles = []
    for raw in (brief.projectName, brief.tenderNo, brief.tenderer):
        s = (raw or "").strip()
        if len(s) >= 4:
            needles.append(s)
    if not needles:
        return text
    kept = [line for line in text.splitlines() if not any(n in line for n in needles)]
    return "\n".join(kept).strip()


def _bundle_hit(item: OutlineItem, scores: list[str], bundle: dict) -> bool:
    title = compact(item.title)
    blob = compact(f"{item.title} {' '.join(scores)} {item.inviteExcerpt or ''}")
    key = compact(str(bundle.get("key") or ""))
    if key and _overlap(title, key):
        return True
    role = str(bundle.get("role") or "")
    if role == "performance" or "业绩" in key:
        cues = ("业绩", "类似项目", "合同", "案例")
    elif role == "person/pm" or "建造" in key or "项目经理" in key:
        cues = ("项目经理", "建造师", "注册")
    elif role == "company" or role.startswith("company"):
        cues = ("资质", "证书", "执照", "信用", "许可证")
    else:
        cues = ()
    return any(c in blob for c in cues)


def pick_library_lines(item: OutlineItem, scores: list[str], bundles: list | None) -> list[str]:
    out: list[str] = []
    for bundle in bundles or []:
        if not isinstance(bundle, dict) or not _bundle_hit(item, scores, bundle):
            continue
        for field in bundle.get("fields") or []:
            if not isinstance(field, dict):
                continue
            name = str(field.get("name") or "").strip()
            value = str(field.get("value") or "").strip()
            if name not in _CITE_FIELDS or not value:
                continue
            try:
                conf = float(field.get("conf", 1))
            except (TypeError, ValueError):
                conf = 1
            if conf < 0.6:
                continue
            line = f"{name}：{value}"
            if line not in out:
                out.append(line)
            if len(out) >= 12:
                return out
    return out


def _hist_ids(kb_ids: list[str] | None) -> list[str]:
    from api.services.tenders.bid_archive import HIST_BASE_ID

    ids: list[str] = []
    for raw in kb_ids or []:
        text = str(raw).strip()
        if text and text not in ids:
            ids.append(text)
    if HIST_BASE_ID not in ids:
        ids.append(HIST_BASE_ID)
    return ids


def fallback_draft(
    item: OutlineItem,
    *,
    facts: str = "",
    scores: list[str] | None = None,
    past: list[str] | None = None,
    reds: list[str] | None = None,
    refs: list[str] | None = None,
    avoid: list[str] | None = None,
) -> str:
    excerpt = (item.inviteExcerpt or item.body or "").strip()
    bits: list[str] = []
    if reds:
        bits.append("红线（不得触碰）：\n" + "\n".join(f"- {s}" for s in reds[:8]))
    if excerpt:
        bits.append("对照招标要求：\n" + excerpt[:800])
    if scores:
        bits.append("评分点：\n" + "\n".join(f"- {s}" for s in scores[:8]))
    if refs:
        bits.append("评分点响应参考，不得照抄项目名。")
    if facts.strip():
        bits.append("本标已有参数：\n" + facts[:1200])
    else:
        bits.append(f"{PENDING_FACT} 本章尚无报价/产品参数，请人工补写，不要编造数量。")
    if past:
        bits.append("历史写法仅供参考，不得照抄项目名和数量。")
    if avoid:
        bits.append("禁止再犯：\n" + "\n".join(f"- {s}" for s in avoid[:6]))
    if not excerpt and not facts.strip() and not reds:
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


async def _verdicts(db: AsyncSession | None, hits) -> dict:
    if db is None:
        return {}
    ids = []
    for hit in hits or []:
        if not isinstance(hit, dict):
            continue
        doc_id = str(hit.get("doc_id") or "").strip()
        if doc_id and doc_id not in ids:
            ids.append(doc_id)
    if not ids:
        return {}
    from sqlalchemy import select

    from db.models.tender import TenderBidMark

    rows = (
        await db.execute(select(TenderBidMark).where(TenderBidMark.doc_public_id.in_(ids)))
    ).scalars().all()
    return {
        m.doc_public_id: {
            "verdict": m.verdict or "",
            "problemTags": list(m.problem_tags or []),
            "note": m.note or "",
        }
        for m in rows
        if m.doc_public_id
    }


async def _search_hits(db, search, query: str, kb_ids, *, top_k: int):
    if search is None:
        if db is None:
            return []
        from api.services.knowledge.ingest import search_chunks

        search = search_chunks
    q = (query or "").strip()
    if not q:
        return []
    try:
        hits = await search(db, query=q, top_k=top_k, kb_ids=_hist_ids(kb_ids))
    except Exception as exc:
        logger.warning("tech draft retrieve skipped: %s", exc)
        return []
    return hits or []


async def load_past(
    db: AsyncSession | None,
    item: OutlineItem,
    kb_ids: list[str] | None = None,
    *,
    search=None,
    brief: BidBrief | None = None,
) -> tuple[list[str], list[str]]:
    q = f"{item.title or ''} {(item.inviteExcerpt or item.body or '')[:80]}".strip()
    hits = await _search_hits(db, search, q, kb_ids, top_k=8)
    if not hits:
        return [], []
    samples, avoid = rank_past_hits(hits, await _verdicts(db, hits))
    clean = []
    for text in samples:
        kept = scrub_fragment(text, brief)
        if kept and kept not in clean:
            clean.append(kept)
    return clean[:3], avoid


async def retrieve_past_fragments(
    db: AsyncSession | None,
    item: OutlineItem,
    kb_ids: list[str] | None = None,
    *,
    search=None,
    brief: BidBrief | None = None,
) -> list[str]:
    samples, _avoid = await load_past(db, item, kb_ids, search=search, brief=brief)
    return samples


async def score_refs(
    db: AsyncSession | None,
    scores: list[str],
    kb_ids: list[str] | None = None,
    *,
    search=None,
    brief: BidBrief | None = None,
) -> tuple[list[str], int]:
    refs: list[str] = []
    hit_n = 0
    for score in scores[:4]:
        q = (score or "").strip()[:80]
        if len(compact(q)) < 4:
            continue
        hits = await _search_hits(db, search, q, kb_ids, top_k=2)
        if not hits:
            continue
        samples, _avoid = rank_past_hits(hits, await _verdicts(db, hits))
        added = False
        for text in samples[:2]:
            kept = scrub_fragment(text, brief)
            if not kept or kept in refs:
                continue
            refs.append(kept)
            added = True
            if len(refs) >= 6:
                return refs, hit_n + (1 if added else 0)
        if added:
            hit_n += 1
    return refs, hit_n


def _user_prompt(
    item: OutlineItem,
    facts: str,
    scores: list[str],
    past: list[str],
    *,
    reds: list[str] | None = None,
    refs: list[str] | None = None,
    avoid: list[str] | None = None,
) -> str:
    parts = []
    if reds:
        parts.append("本标不可触碰红线：\n" + "\n".join(f"- {s}" for s in reds))
    parts.append(f"本章标题：{item.title or '技术方案'}")
    parts.append(f"招标原文：\n{(item.inviteExcerpt or item.body or '').strip() or '（无）'}")
    if scores:
        parts.append("评分点（逐条响应）：\n" + "\n".join(f"- {s}" for s in scores))
    if refs:
        parts.append("评分点响应参考（只参考写法，不是本标事实）：\n" + "\n---\n".join(refs))
    parts.append(f"本标已有事实：\n{facts.strip() or '（无）'}")
    if past:
        parts.append("历史正样本写法（只参考写法，不得照抄项目名和金额）：\n" + "\n---\n".join(past))
    if avoid:
        parts.append("禁止再犯：\n" + "\n".join(f"- {s}" for s in avoid))
    return "\n\n".join(parts)


async def write_chapter(
    item: OutlineItem,
    *,
    facts: str,
    scores: list[str],
    past: list[str],
    client: Any | None = None,
    reds: list[str] | None = None,
    refs: list[str] | None = None,
    avoid: list[str] | None = None,
) -> str:
    if client is None:
        return fallback_draft(item, facts=facts, scores=scores, past=past, reds=reds, refs=refs, avoid=avoid)
    try:
        raw = await client.complete(
            [
                {"role": "system", "content": _DRAFT_SYSTEM},
                {
                    "role": "user",
                    "content": _user_prompt(
                        item, facts, scores, past, reds=reds, refs=refs, avoid=avoid
                    ),
                },
            ],
            mode="fast",
        )
        text = (raw or "").strip()
        if text:
            return text
    except Exception as exc:
        logger.warning("tech draft llm skipped: %s", exc)
    return fallback_draft(item, facts=facts, scores=scores, past=past, reds=reds, refs=refs, avoid=avoid)


async def _library_bundles(db: AsyncSession | None, catalog_facts) -> list:
    if catalog_facts is not None:
        return list(catalog_facts)
    if db is None:
        return []
    try:
        from api.services.tenders.library_kb import library_facts

        return await library_facts(db)
    except Exception as exc:
        logger.warning("tech library facts skipped: %s", exc)
        return []


async def draft_tech(
    brief: BidBrief,
    *,
    db: AsyncSession | None = None,
    kb_ids: list[str] | None = None,
    item_id: str | None = None,
    client: Any | None = None,
    search=None,
    catalog_facts=None,
) -> tuple[BidBrief, list[str]]:
    notes: list[str] = []
    items = hydrate_tech_excerpts(list(brief.outlineItems or []))
    if not item_id:
        items = ensure_tech_chapters(items, brief)
    brief = brief.model_copy(update={"outlineItems": items})
    quote = quote_facts(brief)
    bundles = await _library_bundles(db, catalog_facts)
    reds = tech_red_lines(brief)
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
        scores = scores_for(item, brief)
        lib = pick_library_lines(item, scores, bundles)
        facts = quote
        if lib:
            facts = "\n".join(p for p in (quote, "\n".join(lib)) if p)
            notes.append(f"{item.title}引用资料库 {len(lib)} 条证照/业绩")
        past, avoid = await load_past(db, item, kb_ids, search=search, brief=brief)
        refs, score_n = await score_refs(db, scores, kb_ids, search=search, brief=brief)
        refs = [r for r in refs if r not in set(past)]
        if score_n:
            notes.append(f"{item.title}按 {score_n} 个评分点检索到参考")
        if avoid:
            notes.append(f"{item.title}避开 {len(avoid)} 条负样本问题")
        if past:
            notes.append(f"{item.title}引用了 {len(past)} 段历史写法")
        text = await write_chapter(
            item,
            facts=facts,
            scores=scores,
            past=past,
            client=client,
            reds=reds,
            refs=refs,
            avoid=avoid,
        )
        out.append(apply_chapter_draft(item, text))
        done += 1
    brief = brief.model_copy(update={"outlineItems": out})
    if not item_id:
        brief = draft_deviations(brief, quote)
        notes.append(f"已起草 {done} 章技术标草稿")
    elif done:
        title = next((i.title for i in out if (i.id or "") == item_id), "")
        notes.append(f"已重写「{title}」")
    return brief, notes
