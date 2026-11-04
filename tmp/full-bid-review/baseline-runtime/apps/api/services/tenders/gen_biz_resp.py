"""Draft responses to commercial clauses without changing the bid outline."""

from __future__ import annotations

import json
import logging
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.services.tenders.assets import load_invitation_text
from api.services.tenders.schema import BidBrief, BidRequirement, DeviationLine, OutlineItem
from common.errors import AppError, ErrorCode
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
    "评分",
    "得分",
)
_PRIMARY_WORDS = ("付款", "合同条款", "合同条件", "响应期", "有效期", "商务偏离", "商务承诺")
_EVIDENCE_WORDS = ("资质", "资格", "信誉", "业绩", "营业执照", "财务", "信用", "承诺")
_SECONDARY_WORDS = ("保证金", "交货", "工期", "质保", "保修", "优惠")
_SUBSTANTIAL = re.compile(r"不得|必须|应|需提供|须|★|※")
_SCORE_WORDS = ("价格", "业绩", "资信", "承诺", "优惠", "商务")
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
    title = re.sub(r"\s+", "", item.title or "")
    if item.kind == "biz_dev" or "商务偏离" in title or "商务差异" in title:
        return "deviation"
    if "优惠" in title or "特殊承诺" in title:
        return "offer"
    if item.kind == "commitment_copy" or "响应承诺" in title or "商务响应" in title:
        return "commitment"
    return ""


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

        payload = extract_json_object(raw, prefer_keys=("clauses", "chapters"))
    if (
        not isinstance(payload, dict)
        or not isinstance(payload.get("clauses"), list)
        or not isinstance(payload.get("chapters"), list)
    ):
        raise AppError(ErrorCode.VALIDATION, "商务响应缺少条款或章节", status_code=422)
    return payload


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
    by_text = {re.sub(r"\s+", "", row.requirement): row for row in rows}
    lines = ["商务响应完整性核对表", "条款｜是否已响应｜偏离｜说明"]
    gaps = 0
    for req in substantial:
        row = by_text.get(re.sub(r"\s+", "", req.text))
        complete = bool(
            row
            and row.response
            and "【待补充】" not in row.response
            and "待确认" not in row.response
        )
        deviation = (row.deviation if row else "待确认") or "待确认"
        if deviation in {"负偏离", "待确认"}:
            complete = False
        if not complete:
            gaps += 1
        state = "已响应" if complete else "【待补充】"
        explanation = (row.explanation if row else "") or "证据或响应待核对"
        if deviation == "负偏离":
            explanation = "重大偏离，须人工复核；" + explanation
        lines.append(f"{req.text}｜{state}｜{deviation}｜{explanation}")
    if not substantial:
        lines.append("【待补充】未识别实质性商务条款，请核对招标商务章")
        gaps += 1
    return "\n".join(lines), gaps


def _response_text(rows: list[DeviationLine]) -> str:
    lines = ["逐条商务响应"]
    for index, row in enumerate(rows, 1):
        lines.append(
            f"{index}. {row.requirement}\n响应：{row.response or '【待补充】'}；"
            f"偏离：{row.deviation or '待确认'}；说明：{row.explanation or '【待补充】'}"
        )
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
    ]
    notes: list[str] = []
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
    if not requirements:
        target = next((i for i in editable if _chapter_role(i) == "commitment"), None)
        if target is None:
            return brief, [*notes, "商务标：未提取到商务要求，核对表【待补充】"]
        updated = [
            item.model_copy(
                update={
                    "draftBody": (
                        "商务响应完整性核对表\n【待补充】未识别商务条款和评分项，请核对招标商务章"
                    ),
                    "draftStatus": "drafted",
                }
            )
            if item is target
            else item
            for item in brief.outlineItems
        ]
        return brief.model_copy(update={"outlineItems": updated}), [
            *notes,
            "商务标：未提取到商务要求，已在核对表标【待补充】",
        ]
    if not editable and (brief.bizDeviationLines or not sections):
        return brief, notes
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
                        "chapters 仅使用给定既有标题，只写本批条款相关内容，不重复其他批次。"
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
                    ),
                },
            ],
            mode="pro",
        )
        payload = _parse(raw)
        for row in payload["clauses"]:
            if not isinstance(row, dict):
                continue
            key = str(row.get("requirement") or "").strip()
            if key in batch_texts and key not in by_req:
                by_req[key] = row
        for row in payload["chapters"]:
            if not isinstance(row, dict):
                continue
            title = str(row.get("title") or "").strip()
            body = str(row.get("body") or "").strip()
            if title in body_parts and body:
                body_parts[title].append(body)
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
    bodies = {title: "\n\n".join(parts) for title, parts in body_parts.items()}
    completeness, gap_count = _completeness_table(substantial, rows)
    target = next(
        (i for i in editable if _chapter_role(i) == "commitment"),
        next((i for i in editable if _chapter_role(i) == "offer"), None),
    )
    if target is None:
        notes.append("商务标：没有可填写的承诺章节，完整性核对表【待补充】，未覆盖人工编辑")
    if gap_count:
        notes.append(f"商务标：{gap_count} 条实质性条款待补充或存在偏离，已在核对表标出")
    updated = []
    for item in brief.outlineItems:
        body = bodies.get(item.title, "")
        if item in editable and _chapter_role(item) != "deviation":
            if body and _chapter_role(item) == "offer" and not (brief.extraNote or "").strip():
                body = "【待补充】未提供可核实的商务优惠条件或特殊承诺。"
            known_numbers = set(re.findall(r"\d+(?:\.\d+)?", invitation + evidence))
            safe_numbers = all(n in known_numbers for n in re.findall(r"\d+(?:\.\d+)?", body))
            if not safe_numbers:
                body = "【待补充】正文包含未核实数量或金额，请人工核对。"
            if target is not None and item.title == target.title:
                body = "\n\n".join(
                    part for part in (body, _response_text(rows), completeness) if part
                )
                if not invitation:
                    body = "【待补充】邀请书正文为空，请核对商务条款来源。\n\n" + body
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
