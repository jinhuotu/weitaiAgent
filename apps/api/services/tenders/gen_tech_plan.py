"""Draft text for existing technical outline items before document assembly.

This module never changes the outline. Uploaded material and invitation text are
evidence; retrieved documents provide writing style only.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from api.services.tenders.assets import load_invitation_text, tender_assets_dir
from api.services.tenders.categories import is_tech_chapter_kind
from api.services.tenders.schema import BidBrief, BidRequirement, DeviationLine
from common.errors import AppError, ErrorCode

logger = logging.getLogger("api.tenders.gen_tech_plan")
_SOURCE_ID = re.compile(r"^[a-f0-9]{16}$")
_MAX_SOURCE_BYTES = 20 * 1024 * 1024
_MAX_SOURCE_CHARS = 24_000
_ROUTE_TOPICS = {
    "goods": "技术参数逐条响应；技术响应/偏离表；配置说明；证明材料清单",
    "service": (
        "总体服务方案；质量保证体系；应急预案"
        "（风险识别、应对动作、响应时间线、物资清单）；团队配置"
    ),
    "engineering": (
        "施工部署；主要施工方案及技术措施；质量保证；安全生产；"
        "进度计划；应急预案；文明施工与环保；资源配备"
    ),
}
_ROUTE_LABELS = {"goods": "货物", "service": "服务", "engineering": "工程"}


def _source_dir() -> Path:
    path = tender_assets_dir() / "tech-plan-sources"
    path.mkdir(parents=True, exist_ok=True)
    return path


async def save_tech_source(data: bytes, filename: str) -> dict[str, str]:
    """Parse a user DOCX/PDF once and keep its extracted text for this bid."""
    ext = Path(filename or "").suffix.lower().lstrip(".")
    if ext not in {"docx", "pdf"} or not data or len(data) > _MAX_SOURCE_BYTES:
        raise AppError(
            ErrorCode.VALIDATION, "技术资料须为非空且不超过 20 MB 的 docx/pdf", status_code=422
        )
    source_id = uuid4().hex[:16]
    path = _source_dir() / f"{source_id}.{ext}"
    path.write_bytes(data)
    try:
        from api.services.knowledge.parsers import extract_text_from_file

        extracted = await extract_text_from_file(path, ext=ext)
        text = (extracted.text or "").strip()[:_MAX_SOURCE_CHARS]
        if not text:
            raise AppError(
                ErrorCode.VALIDATION,
                "技术资料未提取到文字，请上传可读取的 docx/pdf",
                status_code=422,
            )
        (_source_dir() / f"{source_id}.txt").write_text(text, encoding="utf-8")
        (_source_dir() / f"{source_id}.meta.json").write_text(
            json.dumps({'filename': Path(filename).name}, ensure_ascii=False), encoding='utf-8',
        )
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return {"sourceId": source_id, "filename": Path(filename).name, "chars": str(len(text))}


def _user_sources(ids: list[str], *, brief: BidBrief, notes: list[str]) -> str:
    from api.services.tenders.source_match import match_source, require_source_match

    parts = []
    for raw in ids[:5]:
        source_id = str(raw or "").strip().lower()
        if not _SOURCE_ID.fullmatch(source_id):
            notes.append('技术资料来源：无效资料编号；未匹配，未采用')
            continue
        path = _source_dir() / f"{source_id}.txt"
        label = source_id
        metadata = _source_dir() / f'{source_id}.meta.json'
        if metadata.is_file():
            try:
                label = str(json.loads(metadata.read_text(encoding='utf-8')).get('filename') or label)
            except (ValueError, AttributeError):
                pass
        if not path.is_file():
            notes.append(f'技术资料来源：{label}；未匹配，文件不存在，未采用')
            continue
        text = path.read_text(encoding='utf-8')[:_MAX_SOURCE_CHARS]
        title = next((line.strip() for line in text.splitlines() if line.strip()), '')[:120]
        result = require_source_match(match_source(
            brief, title=title, text=text, source_name=label,
        ))
        status = {'matched': '已匹配', 'unmatched': '未匹配', 'unverified': '待核验'}[result.status]
        notes.append(f'技术资料来源：{label}；{status}；'
                     + ('与本次标书不匹配，未采用' if result.status == 'unmatched' else '校验通过')
                     + f'（{result.reason}）')
        if result.status != 'unmatched':
            parts.append(text)
    return "\n\n".join(parts)[:_MAX_SOURCE_CHARS]


def _facts(brief: BidBrief) -> str:
    rows = []
    for label, value in (("项目名称", brief.projectName), ("项目内容", brief.bidContent)):
        if str(value or "").strip():
            rows.append(f"{label}：{str(value).strip()}")
    # Schema defaults are not evidence of a promised duration or quality target.
    if brief.deliveryDays != 30:
        rows.append(f"工期/交货期：{brief.deliveryDays} 日")
    if brief.quality and brief.quality != "合格":
        rows.append(f"质量目标：{brief.quality}")
    return "\n".join(rows) or "（未提供可核实的项目事实）"


def _style_guide(samples: list[str]) -> str:
    """Keep only reusable structure, never historical names, amounts or numbers."""
    if not samples:
        return "（无）"
    blob = "\n".join(samples)
    cues = []
    if re.search(r"(?:^|\n)\s*(?:[一二三四五六七八九十]|\d+)[、.)．]", blob):
        cues.append("按编号步骤组织")
    for label, words in (
        ("施工工序与技术控制点", ("工序", "工艺", "技术措施")),
        ("质量检查与验收记录", ("质量", "验收", "检查")),
        ("安全风险与应急处置", ("安全", "风险", "应急")),
        ("进度节点与资源协调", ("进度", "资源", "协调")),
        ("文明施工与环境保护", ("文明施工", "环保", "扬尘")),
    ):
        if any(word in blob for word in words):
            cues.append(label)
    return "；".join(cues) or "采用先说明部署、再说明实施措施、最后说明检查办法的段落结构"


def _technical_requirements(brief: BidBrief, invitation: str) -> list[BidRequirement]:
    from api.services.tenders.classifier import extract_requirements

    rows = list(brief.bidRequirements or [])
    rows.extend(_scoring_requirements(invitation))
    rows.extend(extract_requirements(invitation))
    seen = set()
    out = []
    for row in rows:
        text = (row.text or "").strip()
        if not text or text in seen:
            continue
        if row.volume == "business" or row.category in {
            "qualification",
            "performance",
            "financial",
        }:
            continue
        if (
            row.volume != "technical"
            and row.category not in {"scoring", "technical"}
            and row.targetKind not in {"tech_plan", "tech_chapter", "tech_dev"}
        ):
            continue
        seen.add(text)
        out.append(row)
    return out[:60]


def _scoring_requirements(invitation: str) -> list[BidRequirement]:
    """Recover technical scoring rows that the general requirement classifier may skip."""
    in_technical = False
    out = []
    for raw in (invitation or "").splitlines():
        line = raw.strip().strip("| ")
        compact = re.sub(r"\s+", "", line)
        if re.search(r"技术(?:部分|评分|评审|得分|指标)", compact):
            in_technical = True
        elif re.search(r"(?:商务|价格|报价)(?:部分|评分|评审|得分)", compact):
            in_technical = False
        if not in_technical or not 8 <= len(compact) <= 180:
            continue
        if not re.search(r"(?:评分|得分|分值|\d+(?:\.\d+)?分|技术参数|技术指标)", compact):
            continue
        if re.fullmatch(r"技术(?:部分|评分|评审|得分|指标)[：:]?", compact):
            continue
        out.append(
            BidRequirement(
                id=f"ts{len(out) + 1:02d}",
                text=compact[:120],
                category="scoring",
                strength=(
                    "mandatory" if any(mark in compact for mark in ("★", "※", "必须", "不得"))
                    else "important" if any(mark in compact for mark in ("▲", "重要性", "重要技术参数"))
                    else "general"
                ),
                volume="technical",
            )
        )
        if len(out) >= 30:
            break
    return out


def _score_point_kind(req: BidRequirement) -> str:
    text = req.text or ""
    if req.category == "format" or any(
        mark in text for mark in ("排版", "页码", "暗标", "格式要求")
    ):
        return "格式要求"
    if req.strength == "mandatory" or any(mark in text for mark in ("★", "※", "必须", "不得", "应当")):
        return "必须响应"
    if req.strength == "important" or any(mark in text for mark in ("▲", "重要性", "重要技术参数")):
        return "重要响应（负偏离扣分）"
    return "加分项" if req.category == "scoring" or req.scoreWeight is not None else "必须响应"


def _scorecard(
    requirements: list[BidRequirement], titles: list[str], deviations: list[DeviationLine]
) -> str:
    """Put the score map in existing body text, never in the outline or TOC."""
    lines = ["技术得分点对照表", "类型｜招标技术得分点｜响应及证明｜所在章节"]
    if not requirements:
        lines.append("【待补充】｜未识别到技术评分项，请核对招标评分办法｜【待补充】｜【待补充】")
        return "\n".join(lines)
    by_requirement = {re.sub(r"\s+", "", d.requirement): d for d in deviations}
    for req in requirements:
        hit = by_requirement.get(re.sub(r"\s+", "", req.text))
        chapter = next((title for title in titles if title in (hit.response if hit else "")), "")
        chapter = chapter or (req.targetTitle if req.targetTitle in titles else "【待补充】")
        response = (hit.response if hit else "").strip() or "【待补充】"
        lines.append(f"{_score_point_kind(req)}｜{req.text}｜{response}｜{chapter}")
    return "\n".join(lines)


def _decode_payload(raw: str, titles: list[str]) -> tuple[list[tuple[str, str]], list[dict]]:
    text = (raw or "").lstrip("\ufeff \t\r\n")
    try:
        # Some reasoning models append analysis after a complete JSON answer.
        # raw_decode preserves Chinese quotes inside the JSON strings.
        payload, _ = json.JSONDecoder().raw_decode(text)
    except json.JSONDecodeError:
        from api.services.layouts.parse import extract_json_object

        try:
            payload = extract_json_object(raw, prefer_keys=("chapters", "deviations"))
        except AppError as exc:
            if exc.status_code != 422:
                raise
            payload = {}
    if not isinstance(payload, dict):
        payload = {}
    chapters = payload.get("chapters")
    if not isinstance(chapters, list):
        chapters = []
    pairs = []
    for row in chapters:
        if isinstance(row, dict):
            title, body = row.get("title"), row.get("body")
            if not isinstance(title, str) or not isinstance(body, str):
                continue
            title, body = title.strip(), body.strip()
            if title in titles and body and title not in [p[0] for p in pairs]:
                pairs.append((title, body))
    # 空/缺失章节交给批次调用方按原目录占位，保留其余有效正文和偏离项。
    return pairs, payload.get("deviations") if isinstance(payload.get("deviations"), list) else []


async def draft_technical_plan(
    db: AsyncSession,
    brief: BidBrief,
    *,
    client=None,
    recall=None,
) -> tuple[BidBrief, list[str]]:
    """Return a copy with only existing, unedited technical bodies filled."""
    from api.services.tenders.quote import validate_quote_source

    brief = brief.model_copy(deep=True)
    quote_notes = validate_quote_source(brief)
    editable = [
        i
        for i in brief.outlineItems
        if not i.skipped
        and is_tech_chapter_kind(i.kind)
        and (i.draftStatus or "").lower() not in {"edited", "manual"}
        and (not (i.draftBody or "").strip() or i.draftStatus == "drafted")
    ]
    invitation = load_invitation_text(brief.invitationId)
    source_notes = quote_notes + (
        ["技术标：邀请书正文为空，技术评分项依据【待补充】；请重新识别邀请书"]
        if not invitation
        else []
    )
    user_source_text = _user_sources(
        getattr(brief, 'techPlanSourceIds', []), brief=brief, notes=source_notes,
    )
    requirements = _technical_requirements(brief, invitation)
    from api.services.tenders.classifier import classify_procurement_type

    route = classify_procurement_type(
        "\n".join((brief.techPlanNote or "", brief.bidContent or "", invitation)),
        [i.title for i in brief.outlineItems],
    )
    if not editable:
        covered = {re.sub(r"\s+", "", d.requirement) for d in brief.deviationLines}
        deviations = list(brief.deviationLines)
        for req in requirements:
            if re.sub(r"\s+", "", req.text) in covered:
                continue
            chapter = (
                req.targetTitle if req.targetTitle in {i.title for i in brief.outlineItems} else ""
            )
            deviations.append(
                DeviationLine(
                    seq=str(len(deviations) + 1),
                    requirement=req.text,
                    response=f"【待补充】；证明材料：【待补充】（见「{chapter}」）"
                    if chapter
                    else "【待补充】；证明材料：【待补充】（章节位置【待补充】）",
                    deviation="待确认",
                )
            )
        return brief.model_copy(update={"deviationLines": deviations}), [
            *source_notes,
            "技术章节均已人工编辑或无可填章节；技术得分点对照表【待补充】，未覆盖原文"
        ]
    knowledge = []
    if getattr(brief, "techPlanKbIds", []):
        if recall is None:
            from api.services.tenders.extract import _recall_knowledge

            recall = _recall_knowledge
        knowledge, _ = await recall(
            db,
            invitation,
            getattr(brief, "techPlanKbIds", []),
            query=f"{_ROUTE_LABELS[route]}技术方案 {_ROUTE_TOPICS[route]}",
            quiet=True,
        )
    from api.services.tenders.tech_draft import load_past

    historical = []
    if db is not None:
        for item in editable[:8]:
            samples, _ = await load_past(db, item, getattr(brief, "techPlanKbIds", []), brief=brief)
            historical.extend(samples[:2])
    style = _style_guide(
        [str(x.get("content") or "") for x in knowledge[:4] if isinstance(x, dict)] + historical[:8]
    )
    if client is None:
        from api.services.models.runtime import build_llm_client

        client = await build_llm_client(db, "pro")
    titles = [i.title for i in editable]
    user_input = "\n\n".join(
        x
        for x in (
            (brief.techPlanNote or "").strip(),
            user_source_text,
        )
        if x
    )
    sections = [
        "【用户输入，最高优先级】\n" + (user_input or "（无）"),
        "【招标技术要求及评分点】\n"
        + ("\n".join(f"{n + 1}. {r.text}" for n, r in enumerate(requirements)) or "（未提取到）"),
        "【知识库与历史技术标写法参考，仅保留结构，不含历史数据】\n" + style,
        "【本项目事实】\n" + _facts(brief),
        f"【采购类型】{_ROUTE_LABELS[route]}类\n【对应得分点模板】{_ROUTE_TOPICS[route]}",
        "【得分点分类】\n" + (
            "\n".join(f"{_score_point_kind(r)}：{r.text}" for r in requirements)
            or "【待补充】未识别到技术评分点，请在既有技术章节中标明待核对"
        ),
    ]
    pairs: list[tuple[str, str]] = []
    devs: list[dict] = []
    missing_titles: list[str] = []
    generated_count = 0
    for start in range(0, len(titles), 2):
        group = titles[start : start + 2]
        raw = await client.complete(
            [
                {
                    "role": "system",
                    "content": (
                        "你是技术标起草员。只为给出的既有章节起草正文，不新增、合并或改名章节。"
                        "用户输入优先，招标要求其次，项目事实用于核对，知识库只借鉴写法。"
                        "逐条响应评分点，每章写出具体部署、措施、检查办法。"
                        f"本标为{_ROUTE_LABELS[route]}类；按对应模板覆盖：{_ROUTE_TOPICS[route]}。"
                        "先解构必须响应、加分项及格式要求，再将其落在允许填充的既有章节正文中；"
                        "模板主题只能作为正文小节，不得新增目录章节。"
                        "缺少可核实的工期、人员、设备、业绩数量等事实时写【待补充】，不得编造。"
                        "输出 JSON 对象：chapters=[{title,body}]；"
                        "deviations=[{requirement,response,deviation,chapter,evidence}]。"
                        "技术需求逐条写响应或偏离，并标所在章节与证明材料；缺证据填【待补充】。"
                        "每个偏离项只可引用给出的章节标题；未能证实满足时写待补充，不得宣称完全响应。"
                    ),
                },
                {
                    "role": "user",
                    "content": "\n\n".join(
                        [
                            *sections,
                            "【本轮仅允许填充的既有章节标题，按此顺序】\n" + "\n".join(group),
                        ]
                    ),
                },
            ],
            mode="pro",
        )
        group_pairs, group_devs = _decode_payload(raw, group)
        generated_count += len(group_pairs)
        returned = {title for title, _ in group_pairs}
        pairs.extend(group_pairs)
        for title in group:
            if title not in returned:
                missing_titles.append(title)
                pairs.append((
                    title,
                    f"【待补充】「{title}」技术方案正文未完整生成，请结合本项目招标要求补充。",
                ))
        devs.extend(group_devs)
    drafted = dict(pairs)
    # Never overwrite manually entered deviation rows.
    existing = {re.sub(r"\s+", "", d.requirement) for d in brief.deviationLines}
    deviations = list(brief.deviationLines)
    for req in requirements:
        if re.sub(r"\s+", "", req.text) in existing:
            continue
        hit = next(
            (
                d
                for d in devs
                if isinstance(d, dict) and str(d.get("requirement") or "").strip() == req.text
            ),
            None,
        )
        chapter = str((hit or {}).get("chapter") or "").strip()
        valid_titles = [i.title for i in brief.outlineItems]
        if chapter not in valid_titles:
            chapter = req.targetTitle if req.targetTitle in valid_titles else ""
        response = str((hit or {}).get("response") or "【待补充】").strip()
        deviation = str((hit or {}).get("deviation") or "待确认").strip()
        evidence = str((hit or {}).get("evidence") or "【待补充】").strip()
        deviations.append(
            DeviationLine(
                seq=str(len(deviations) + 1),
                requirement=req.text,
                response=f"{response}；证明材料：{evidence}（见「{chapter}」）"
                if chapter
                else f"{response}；证明材料：{evidence}（章节位置【待补充】）",
                deviation=deviation,
            )
        )
    first_title = titles[0]
    drafted[first_title] = "\n\n".join(
        part
        for part in (
            _scorecard(requirements, [i.title for i in brief.outlineItems], deviations),
            "【待补充】邀请书正文为空，请核对技术评分项来源。" if not invitation else "",
            drafted[first_title],
        )
        if part
    )
    updated = [
        i.model_copy(update={"draftBody": drafted[i.title], "draftStatus": "drafted"})
        if i.title in drafted and i in editable
        else i
        for i in brief.outlineItems
    ]
    notes = [
        *source_notes,
        f"技术方案已填充 {generated_count} 个既有章节",
    ]
    if missing_titles:
        notes.append(
            f"技术方案：{len(missing_titles)} 个章节正文未完整返回，"
            "已在原章节标【待补充】，投标文件继续生成"
        )
    return brief.model_copy(update={"outlineItems": updated, "deviationLines": deviations}), notes
