"""Draft responses to commercial clauses without changing the bid outline."""

from __future__ import annotations

import json
import logging
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.services.tenders.assets import load_invitation_text
from api.services.tenders.schema import BidBrief, BidRequirement, DeviationLine, OutlineItem
from common.errors import AppError
from db.models.knowledge import KnowledgeDocument

_BIZ_WORDS = (
    "付款",
    "合同",
    "有效期",
    "响应期",
    "资质",
    "资格",
    "信誉",
    "营业执照",
    "业绩",
    "承诺",
    "保证金",
    "交货",
    "工期",
    "质保",
    "保修",
    "优惠",
    "交付",
    "验收",
    "售后",
    "培训",
    "资信",
    "价格",
)
_REQ_WORDS = (
    "应",
    "须",
    "必须",
    "要求",
    "不少于",
    "不得",
    "提供",
    "具备",
    "期限",
    "比例",
    "金额",
)
_PRIMARY_WORDS = ("付款", "合同条款", "合同条件", "响应期", "有效期", "商务偏离", "商务承诺")
_EVIDENCE_WORDS = ("资质", "资格", "信誉", "业绩", "营业执照", "财务", "信用", "承诺")
_SECONDARY_WORDS = ("保证金", "交货", "工期", "质保", "保修", "优惠")
_SUBSTANTIAL = re.compile(r"不得|必须|应|需提供|须|★|※|▲|重要性")
_SCORE_WORDS = ("价格", "业绩", "资信", "承诺", "优惠", "商务")
_REVIEW_RULE = re.compile(
    r"(?:商务|技术|价格|综合)(?:标|部分)?得分(?:等于|[=＝])"
    r"|评审(?:的)?业绩规定|评审规则|评审程序|评标办法"
    r"|评分标准|评分方法|计分方法|分值构成|得分计算|满分|加分|扣分|得\d+(?:\.\d+)?分"
)
_RESPONSE_ACTION = re.compile(
    r"(?:应当|应|须|必须|需|不得).{0,12}(?:提供|提交|具备|满足|符合|承诺|执行|支付|完成|保证)"
    r"|(?:提供|提交).{0,30}(?:证明|合同|证书|承诺|材料|复印件|扫描件)"
)
logger = logging.getLogger("api.tenders.gen_biz_resp")


def business_requirements(brief: BidBrief, invitation: str) -> list[BidRequirement]:
    """Keep only commercial clauses, including lines the generic classifier missed."""
    from api.services.tenders.classifier import extract_requirements

    candidates = [*brief.bidRequirements, *extract_requirements(invitation)]
    for raw in (invitation or "").splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        if (
            10 <= len(line) <= 180
            and any(w in line for w in _BIZ_WORDS)
            and any(w in line for w in _REQ_WORDS)
        ):
            candidates.append(BidRequirement(text=line, volume="business", category="commercial"))
    seen: set[str] = set()
    ranked: list[tuple[int, BidRequirement]] = []
    for row in candidates:
        line = (row.text or "").strip()
        key = re.sub(r"\s+", "", line)
        if not key or key in seen or row.volume == "technical" or row.category == "technical":
            continue
        if not any(w in line for w in _BIZ_WORDS):
            continue
        if (
            row.volume != "business"
            and row.category not in {"qualification", "performance", "financial", "commercial"}
            and not any(w in line for w in _BIZ_WORDS)
        ):
            continue
        if any(w in line for w in ("目录", "装订", "页码", "投标文件格式")):
            continue
        if any(w in line for w in ("评标委员会", "评审因素", "电子投标文件制作工具", "定标委员会")):
            continue
        # 评分规则留给 _business_scores 作上下文，只有可履行/举证的义务才逐条响应。
        if (row.category == "scoring" or _REVIEW_RULE.search(key)) and not _RESPONSE_ACTION.search(key):
            continue
        priority = (
            4
            if row.volume == "business" or row.targetKind == "biz_dev"
            else 3
            if any(w in line for w in _PRIMARY_WORDS)
            else 2
            if row.category == "scoring" or any(w in line for w in _EVIDENCE_WORDS)
            else 1
            if any(w in line for w in _SECONDARY_WORDS)
            else 0
        )
        if not priority:
            continue
        seen.add(key)
        ranked.append((priority, row))
    ranked.sort(key=lambda pair: -pair[0])
    critical = [
        row for _, row in ranked if row.strength == "mandatory" or _SUBSTANTIAL.search(row.text)
    ]
    ordinary = [
        row
        for _, row in ranked
        if row.strength != "mandatory" and not _SUBSTANTIAL.search(row.text)
    ]
    return [*critical, *ordinary[:50]]


def _business_scores(brief: BidBrief, invitation: str) -> list[tuple[str, str]]:
    """Extract commercial score rows and their stated weights without inventing scores."""
    candidates = [
        (row.text, f"{row.scoreWeight:g}分" if row.scoreWeight is not None else "")
        for row in brief.bidRequirements
        if row.volume != "technical"
        and row.category == "scoring"
        and any(word in (row.text or "") for word in _SCORE_WORDS)
    ]
    in_business = False
    for raw in (invitation or "").splitlines():
        line = re.sub(r"\s+", "", raw).strip("| ")
        if re.search(r"(?:商务|价格)(?:评分|评审|得分|部分)", line):
            in_business = True
        elif re.search(r"技术(?:评分|评审|得分|部分)", line):
            in_business = False
        if in_business and 8 <= len(line) <= 180 and any(w in line for w in _SCORE_WORDS):
            if re.search(r"\d+(?:\.\d+)?分|评分|得分|分值", line):
                candidates.append((line[:120], ""))
    result = []
    seen = set()
    for text, stated_weight in candidates:
        key = re.sub(r"\s+", "", text)
        if not key or key in seen:
            continue
        seen.add(key)
        weight = re.search(r"(\d+(?:\.\d+)?)\s*分", text)
        result.append(
            (text, stated_weight or (f"{weight.group(1)}分" if weight else "权重【待补充】"))
        )
        if len(result) >= 30:
            break
    return result


def _substantial_clauses(requirements: list[BidRequirement]) -> list[BidRequirement]:
    return [
        row
        for row in requirements
        if row.strength == "mandatory" or _SUBSTANTIAL.search(row.text or "")
    ]


def _writing_style(docs: list[dict]) -> str:
    """Reduce retrieved examples to style cues; never place historical facts in the prompt."""
    blob = "\n".join(str(row.get("content") or "")[:2000] for row in docs if isinstance(row, dict))
    if not blob:
        return ""
    cues = []
    if re.search(r"(?:^|\n)\s*(?:[一二三四五六七八九十]|\d+)[、.．]", blob):
        cues.append("按条款编号逐项回应")
    for cue, words in (
        ("先列承诺，再列履约依据", ("承诺", "履约")),
        ("将证明材料列在响应之后", ("证明材料", "附件", "佐证")),
        ("对付款、交付、验收分别说明", ("付款", "交付", "验收")),
        ("对售后、培训与质保写明服务动作", ("售后", "培训", "质保")),
    ):
        if any(word in blob for word in words):
            cues.append(cue)
    return "；".join(cues) or "逐条写招标条款、响应动作、证据来源及需核实事项"


async def _won_business_docs(db: AsyncSession | None, docs: list[dict]) -> list[dict]:
    """Use only winning business chapters when recall has database provenance."""
    ids = [str(doc.get("docId") or "").strip() for doc in docs if isinstance(doc, dict)]
    ids = [doc_id for doc_id in ids if doc_id]
    if not ids or db is None:
        return docs
    result = await db.execute(
        select(KnowledgeDocument.public_id, KnowledgeDocument.tags).where(
            KnowledgeDocument.public_id.in_(ids)
        )
    )
    allowed = {
        public_id
        for public_id, tags in result.all()
        if {"tech_bid", "volume:business", "outcome:won"}.issubset(set(tags or []))
    }
    return [doc for doc in docs if str(doc.get("docId") or "") in allowed]


def _chapter_role(item: OutlineItem) -> str:
    from api.services.tenders.business_draft_policy import chapter_role

    return chapter_role(item)


def _evidence(brief: BidBrief, library_facts: list[dict]) -> str:
    lines = [f"项目名称：{brief.projectName}"] if brief.projectName else []
    if brief.bidContent:
        lines.append(f"项目内容：{brief.bidContent}")
    if (brief.extraNote or "").strip():
        lines.append(f"用户商务补充说明：{brief.extraNote.strip()}")
    if brief.bidPriceYuan > 0:
        lines.append(f"用户报价：{brief.bidPriceYuan} 元")
    for row in brief.performanceLines:
        if row.includeInBid and row.projectName and row.projectName not in {"待补充", "【待补充】"}:
            lines.append(
                f"用户选定业绩：{row.projectName}；{row.summary or row.spec or '详情待核'}"
            )
    for fact in library_facts[:12]:
        if not isinstance(fact, dict) or fact.get("key") not in {
            "perf",
            "iso",
            "license",
            "finance",
        }:
            continue
        fields = fact.get("fields") or []
        lines.append(f"资料库 {fact.get('key')}，文件 {fact.get('fileId')}：{str(fields)[:250]}")
    return "\n".join(lines)[:5000] or "（无可核实事实）"


def _parse(raw: str) -> dict:
    text = (raw or "").lstrip("\ufeff \t\r\n")
    try:
        payload, _ = json.JSONDecoder().raw_decode(text)
    except json.JSONDecodeError:
        from api.services.layouts.parse import extract_json_object

        try:
            payload = extract_json_object(raw, prefer_keys=("clauses", "chapters"))
        except AppError as exc:
            if exc.status_code != 422:
                raise
            payload = {}
    if not isinstance(payload, dict):
        payload = {}
    # 缺哪部分就留空交给后续占位，不丢弃另一部分已返回的有效内容。
    return {
        key: payload[key] if isinstance(payload.get(key), list) else []
        for key in ("clauses", "chapters")
    }


def _safe_response(
    requirement: str, response: str, deviation: str, evidence: str
) -> tuple[str, str]:
    """Reject invented numbers and unsupported claims of qualification/performance."""
    supplied_numbers = set(re.findall(r"\d+(?:\.\d+)?", requirement + evidence))
    numbers = re.findall(r"\d+(?:\.\d+)?", response)
    if any(n not in supplied_numbers for n in numbers):
        return "【待补充】响应含未核实的金额或数量，请人工核对", "待确认"
    needs_performance = "业绩" in requirement
    needs_qualification = any(w in requirement for w in ("资质", "资格", "证书", "许可"))
    if needs_performance and "用户选定业绩：" not in evidence and "资料库 perf" not in evidence:
        return "【待补充】业绩证明尚未核实", "待确认"
    if needs_qualification and "资料库 iso" not in evidence and "资料库 license" not in evidence:
        return "【待补充】资格证明尚未核实", "待确认"
    if any(word in requirement for word in ("价格", "报价金额")) and "用户报价：" not in evidence:
        return "【待补充】报价事实尚未核实", "待确认"
    if "优惠" in requirement and "用户商务补充说明：" not in evidence:
        return "【待补充】优惠条件尚未核实", "待确认"
    if not response or "【待补充】" in response or "待确认" in response:
        return response or "【待补充】", "待确认"
    return response, deviation if deviation in {
        "无偏离",
        "正偏离",
        "负偏离",
        "待确认",
    } else "待确认"


def _completeness_table(
    substantial: list[BidRequirement], rows: list[DeviationLine]
) -> tuple[str, int]:
    from api.services.tenders.business_render import pending_summary, substantive_response

    by_text = {re.sub(r"\s+", "", row.requirement): row for row in rows}
    gaps = 0
    for req in substantial:
        row = by_text.get(re.sub(r"\s+", "", req.text))
        if not row or not substantive_response(row.response) or row.deviation in {"负偏离", "待确认"}:
            gaps += 1
    if not substantial:
        return "商务响应完整性核对表\n【待补充】未识别实质性商务条款，请核对招标商务章", 1
    return "商务响应完整性核对表\n" + (pending_summary(gaps) if gaps else "实质性商务条款已响应，请核对证明材料。"), gaps


def _response_text(rows: list[DeviationLine]) -> str:
    from api.services.tenders.business_render import pending_summary, substantive_response

    visible = [row for row in rows if substantive_response(row.response)]
    lines = ["逐条商务响应"] if visible else []
    for index, row in enumerate(visible, 1):
        lines.append(f"{index}. {row.requirement}\n响应：{row.response}；偏离：{row.deviation or '待确认'}")
    summary = pending_summary(len(rows) - len(visible))
    if summary:
        lines.append(summary)
    return "\n".join(lines)


async def draft_business_response(
    db: AsyncSession,
    brief: BidBrief,
    *,
    library_facts: list[dict] | None = None,
    kb_ids: list[str] | None = None,
    client=None,
    recall=None,
) -> tuple[BidBrief, list[str]]:
    """Fill only existing, unedited business sections; preserve user-written rows."""
    from api.services.tenders.business_draft_policy import (
        business_contexts,
        chapter_paragraphs,
        protected_business_titles,
    )

    contexts = business_contexts(brief)
    protected = protected_business_titles(brief, contexts)
    if protected:
        # Old record snapshots can still contain the earlier automatic expansion.
        brief = brief.model_copy(update={"outlineItems": [
            item.model_copy(update={"draftBody": "", "draftStatus": "empty"})
            if item.title in protected and item.draftStatus == "drafted" else item
            for item in brief.outlineItems
        ]})
    invitation = load_invitation_text(brief.invitationId)
    requirements = business_requirements(brief, invitation)
    substantial = _substantial_clauses(requirements)
    scoring = _business_scores(brief, invitation)
    sections = [i for i in brief.outlineItems if not i.skipped and _chapter_role(i)]
    editable = [
        i
        for i in sections
        if (i.draftStatus or "").lower() not in {"edited", "manual"}
        and (not (i.draftBody or "").strip() or i.draftStatus == "drafted")
        and i.title not in protected
    ]
    notes: list[str] = []
    if protected:
        notes.append("商务标：已保护原版固定承诺/声明，未自动扩写：" + "、".join(sorted(protected)))
    if not invitation:
        notes.append("商务标：邀请书正文为空，商务条款依据【待补充】；请重新识别邀请书")
    roles = {_chapter_role(i) for i in sections}
    for role, label in (
        ("deviation", "商务偏离表"),
        ("offer", "商务优惠及特殊承诺"),
        ("commitment", "响应承诺正文"),
    ):
        if role not in roles:
            notes.append(f"商务标：大纲无「{label}」对应章节，未擅自新增目录项")
    if not editable:
        return brief, [
            *notes,
            "商务标：没有可自动填写的商务响应章节，需核对的响应【待补充】；已保留人工内容和原大纲",
        ]
    if not requirements:
        updated = [
            item.model_copy(
                update={
                    "draftBody": (
                        "商务响应完整性核对表\n【待补充】未识别可逐条响应的商务条款，请核对招标商务章"
                    ),
                    "draftStatus": "drafted",
                }
            )
            if item in editable
            else item
            for item in brief.outlineItems
        ]
        pending_rows = brief.bizDeviationLines
        if not pending_rows and any(_chapter_role(item) == "deviation" for item in editable):
            pending_rows = [DeviationLine(
                seq="1", requirement="【待补充】未识别可逐条响应的商务条款",
                response="【待补充】请核对招标商务章", deviation="待确认",
            )]
        return brief.model_copy(update={"outlineItems": updated, "bizDeviationLines": pending_rows}), [
            *notes,
            "商务标：未提取到商务要求，已在核对表标【待补充】",
        ]
    evidence = _evidence(brief, library_facts or [])
    selected_ids = [
        str(x).strip()
        for x in (kb_ids if kb_ids is not None else getattr(brief, "bizRespKbIds", []))
        if str(x).strip()
    ]
    style = ""
    if selected_ids:
        if recall is None:
            from api.services.tenders.extract import _recall_knowledge

            recall = _recall_knowledge
        from api.services.tenders.classifier import classify_procurement_type

        kind = classify_procurement_type(invitation, [i.title for i in brief.outlineItems])
        keywords = " ".join(r.text[:24] for r in requirements[:5])
        score_words = " ".join(text[:24] for text, _weight in scoring[:3])
        try:
            docs, _ = await recall(
                db,
                invitation,
                selected_ids,
                query=f"{kind} 已中标 商务响应 商务偏离 承诺 {score_words} {keywords}"[:240],
                quiet=True,
            )
            style = _writing_style(await _won_business_docs(db, docs))
        except Exception as exc:  # noqa: BLE001
            logger.warning("business style recall skipped: %s", exc)
    if style:
        notes.append("商务标：已参考所选知识库的商务响应写法")
    if client is None:
        from api.services.models.runtime import build_llm_client

        client = await build_llm_client(db, "pro")
    titles = [i.title for i in editable]
    by_req: dict[str, dict] = {}
    body_parts: dict[str, list[str]] = {title: [] for title in titles}
    chapter_candidates: dict[str, list[str]] = {title: [] for title in titles}
    chapter_titles = {i.title for i in editable if _chapter_role(i) != "deviation"}
    chapter_gaps: set[str] = set()
    clause_gaps = False
    for offset in range(0, len(requirements), 20):
        batch = requirements[offset : offset + 20]
        batch_texts = {row.text for row in batch}
        batch_substantial = [row.text for row in batch if row in substantial]
        raw = await client.complete(
            [
                {
                    "role": "system",
                    "content": (
                        "你是商务标响应起草员。仅根据招标条款和已核实事实逐条起草。"
                        "每条保留原文 requirement，输出 response、deviation、explanation。"
                        "deviation 仅为无偏离、正偏离、负偏离或待确认。"
                        "不能凭空承诺资质、业绩、金额、优惠或合同条件；证据不足写【待补充】并标待确认；不把不符合项写成无偏离。"
                        "实质性条款逐条核对，优先按招标条件提出正向响应；不能证实履约时标待确认，已知负偏离须如实标出。"
                        "按商务评分项组织响应，但不得编造分值、业绩或优惠。知识库仅参考写法，禁止套用历史项目事实。"
                        "chapters 仅使用给定可写标题，围绕该章节用途起草正式响应正文。"
                        "不得扩写原版固定承诺/声明，不得改变项目经理等专用承诺的用途。"
                        "正文不得出现本批、现阶段、概括响应、起草说明或不作无依据承诺等生成过程用语。"
                        "逐条响应只放 clauses，chapters 不再复制条款或逐条响应；未响应条款不在正文展开。"
                        "营业执照、财务、业绩响应归入对应资格证明，不在合同响应或承诺正文重复罗列。"
                        "无可核实优惠写【待补充】，不得编造优惠。"
                        "只返回 JSON：clauses 含 requirement、response、deviation、explanation；"
                        "chapters 含 title、body。"
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "【用户商务输入，最高优先级】\n"
                        + ((brief.extraNote or "").strip() or "（无）")
                        + "\n【本批招标商务条款】\n"
                        + "\n".join(f"{offset + n + 1}. {r.text}" for n, r in enumerate(batch))
                        + "\n【本批实质条款清单，须逐条响应】\n"
                        + ("\n".join(batch_substantial) or "（无）")
                        + "\n【商务评分项与权重】\n"
                        + (
                            "\n".join(f"{text}（{weight}）" for text, weight in scoring)
                            or "【待补充】未识别商务评分项"
                        )
                        + "\n【所选知识库写法参考，不可引用其项目事实】\n"
                        + (style or "（无）")
                        + "\n【已核实项目及资料库事实】\n"
                        + evidence
                        + "\n【仅允许填充的既有章节】\n"
                        + "\n".join(titles)
                        + "\n【上述章节原文及用途，仅在空缺处补充】\n"
                        + "\n".join(f"{title}：{contexts.get(title, '')[:1200] or '空白响应章节'}"
                                    for title in titles)
                    ),
                },
            ],
            mode="pro",
        )
        payload = _parse(raw)
        answered = set()
        for row in payload["clauses"]:
            if not isinstance(row, dict):
                continue
            key = str(row.get("requirement") or "").strip()
            if key in batch_texts and key not in by_req:
                by_req[key] = row
                if str(row.get("response") or "").strip():
                    answered.add(key)
        if batch_texts - answered:
            clause_gaps = True
        returned_chapters = set()
        for row in payload["chapters"]:
            if not isinstance(row, dict):
                continue
            title = str(row.get("title") or "").strip()
            body = str(row.get("body") or "").strip()
            if title in body_parts and body:
                # Keep only useful paragraphs, never model-generated pending clause copies.
                parts = chapter_paragraphs(body)
                body_parts[title].extend(part for part in parts if part not in body_parts[title])
                if parts:
                    candidate = "\n\n".join(parts)
                    if candidate not in chapter_candidates[title]:
                        chapter_candidates[title].append(candidate)
                    returned_chapters.add(title)
        chapter_gaps.update(chapter_titles - returned_chapters)
    if clause_gaps:
        notes.append("商务标：模型未完整返回逐条响应，缺失条款已标【待补充】，投标文件继续生成")
    if chapter_gaps:
        notes.append("商务标：模型未完整返回章节正文，缺失正文已标【待补充】，投标文件继续生成")
        for title in chapter_gaps:
            if not body_parts[title]:
                body_parts[title].append("【待补充】商务响应章节正文未完整生成，请核对后补充。")
    existing = {re.sub(r"\s+", "", d.requirement) for d in brief.bizDeviationLines}
    rows = list(brief.bizDeviationLines)
    for req in requirements:
        if re.sub(r"\s+", "", req.text) in existing:
            continue
        answer = by_req.get(req.text) or {}
        response, deviation = _safe_response(
            req.text,
            str(answer.get("response") or "").strip(),
            str(answer.get("deviation") or "待确认").strip(),
            evidence,
        )
        explanation = str(answer.get("explanation") or "").strip()
        known_numbers = set(re.findall(r"\d+(?:\.\d+)?", req.text + evidence))
        if not all(n in known_numbers for n in re.findall(r"\d+(?:\.\d+)?", explanation)):
            explanation = "【待补充】说明包含未核实数量或金额"
        rows.append(
            DeviationLine(
                seq=str(len(rows) + 1),
                requirement=req.text,
                response=response,
                deviation=deviation,
                explanation=explanation,
            )
        )
    # Clauses are rendered once, in their evidence section or deviation/checklist table.
    # Do not trust the model to obey the no-duplication instruction in chapter prose.
    def clause_copy(part: str) -> bool:
        text = re.sub(r"\s+", "", part)
        for row in rows:
            requirement = re.sub(r"\s+", "", row.requirement)
            response = re.sub(r"\s+", "", row.response)
            if requirement and requirement in text:
                return True
            if response and (response == text or (len(response) >= 8 and response in text)):
                return True
        return False

    merge_titles = [title for title in titles
                    if title in chapter_titles and len(chapter_candidates[title]) > 1]
    if merge_titles and len(requirements) > 20:
        # Clause batches are not successive paragraphs of a complete chapter.
        # Compose one coherent result rather than concatenating their summaries.
        raw = await client.complete([
            {"role": "system", "content": (
                "本次任务是商务响应章节统稿。只使用给定草稿和已核实事实，按章节用途形成一份连贯正文。"
                "合并重复含义，不增加事实、金额、优惠或合同承诺，不扩写原版固定声明。"
                "不要逐条复制招标要求或响应；证据不足的事项留给核对表。"
                "不得出现本批、现阶段、概括响应、起草说明或无依据承诺等生成过程用语。"
                "只返回JSON：chapters为title、body列表，仅使用给定标题。"
            )},
            {"role": "user", "content": (
                "【已核实事实及用户商务输入】\n" + evidence
                + "\n【章节用途与候选草稿】\n"
                + json.dumps([{
                    "title": title, "original": contexts.get(title, "")[:1200],
                    "drafts": chapter_candidates[title],
                } for title in merge_titles], ensure_ascii=False)[:20000]
            )},
        ], mode="pro")
        composed = {}
        for part in _parse(raw)["chapters"]:
            if isinstance(part, dict) and part.get("title") in merge_titles:
                clean = chapter_paragraphs(str(part.get("body") or ""))
                if clean:
                    composed.setdefault(part["title"], clean)
        for title in merge_titles:
            # A malformed merge must not recreate the original concatenation bug.
            body_parts[title] = composed.get(title) or chapter_paragraphs(chapter_candidates[title][0])
        notes.append(
            "商务标：分批草稿已按章节统一整理"
            if len(composed) == len(merge_titles)
            else "商务标：部分章节统稿未完成，已保留首份有效草稿，请核对后补充"
        )
    bodies = {
        title: "\n\n".join(part for part in parts if not clause_copy(part))
        for title, parts in body_parts.items()
    }
    _, gap_count = _completeness_table(substantial, rows)
    if gap_count:
        notes.append(f"商务标：{gap_count} 条实质性条款待补充或存在偏离，已在核对表标出")
    updated = []
    for item in brief.outlineItems:
        body = bodies.get(item.title, "")
        if item in editable and _chapter_role(item) != "deviation":
            if not body:
                body = "【待补充】本章节正文尚未生成，请核对后补充。"
            if body and _chapter_role(item) == "offer" and not (brief.extraNote or "").strip():
                body = "【待补充】未提供可核实的商务优惠条件或特殊承诺。"
            known_numbers = set(re.findall(r"\d+(?:\.\d+)?", invitation + evidence))
            safe_numbers = all(n in known_numbers for n in re.findall(r"\d+(?:\.\d+)?", body))
            if not safe_numbers:
                body = "【待补充】正文包含未核实数量或金额，请人工核对。"
            updated.append(
                item.model_copy(update={"draftBody": body, "draftStatus": "drafted"})
                if body
                else item
            )
        else:
            updated.append(item)
    headers = brief.bizDevHeaders or (
        ["招标条款", "响应", "偏离", "说明"]
        if any(_chapter_role(i) == "deviation" and not (i.body or "").strip() for i in sections)
        else []
    )
    return brief.model_copy(
        update={"outlineItems": updated, "bizDeviationLines": rows, "bizDevHeaders": headers}
    ), [
        *notes,
        f"商务标：已对照 {len(rows)} 条商务要求起草响应",
    ]
