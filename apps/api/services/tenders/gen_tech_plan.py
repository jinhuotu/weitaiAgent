"""Draft text for existing technical outline items before document assembly.

This module never changes the outline. Uploaded material and invitation text are
evidence; retrieved documents provide writing style only.
"""

from __future__ import annotations

from api.services.tenders.prompts import get_prompt
from api.services.tenders.generation_parallel import complete_json

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
    for label, value in (("投标人", brief.bidderName), ("项目名称", brief.projectName), ("项目内容", brief.bidContent)):
        if str(value or "").strip():
            rows.append(f"{label}：{str(value).strip()}")
    # Schema defaults are not evidence of a promised duration or quality target.
    if brief.deliveryDays > 0:
        rows.append(f"工期/交货期：{brief.deliveryDays} 日")
    if brief.quality:
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
    for row in sorted(rows, key=lambda row: not _is_scoring(row)):
        text = (row.text or "").strip()
        if not text or text in seen:
            continue
        if not _is_scoring(row) and (row.volume == "business" or row.category in {
            "qualification",
            "performance",
            "financial",
        }):
            continue
        if (
            not _is_scoring(row) and row.volume != "technical"
            and row.category not in {"scoring", "technical"}
            and row.targetKind not in {"tech_plan", "tech_chapter", "tech_dev"}
        ):
            continue
        seen.add(text)
        out.append(row)
    return out


def _is_scoring(req: BidRequirement) -> bool:
    return req.category.strip().lower() == 'scoring' or req.scoreWeight is not None


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
    """Self-check display only; never render this text as a chapter body."""
    lines = ["评分点自查表（仅自检，不写入投标正文）", "类型｜招标评分点｜响应及证明｜所在章节"]
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
        and (not (i.draftBody or "").strip() or i.draftStatus == "drafted" or "未完整生成" in (i.draftBody or ""))
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
    all_requirements = _technical_requirements(brief, invitation)
    scoring = [r for r in all_requirements if _is_scoring(r)]
    requirements = [r for r in all_requirements if not _is_scoring(r)][:60]
    score_texts = {re.sub(r'\s+', '', r.text) for r in scoring}
    score_responses = [d for d in brief.deviationLines if re.sub(r'\s+', '', d.requirement) in score_texts]
    brief.deviationLines = [d for d in brief.deviationLines if re.sub(r'\s+', '', d.requirement) not in score_texts]
    brief.techScorecard = _scorecard(scoring, [i.title for i in brief.outlineItems], score_responses)
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
            "技术章节均已人工编辑或无可填章节，未覆盖原文",
            brief.techScorecard,
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
            bidder_name=brief.bidderName,
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
        "【招标技术要求】\n"
        + ("\n".join(f"{n + 1}. {r.text}" for n, r in enumerate(requirements)) or "（未提取到）"),
        "【知识库与历史技术标写法参考，仅保留结构，不含历史数据】\n" + style,
        "【本项目事实】\n" + _facts(brief),
        f"【采购类型】{_ROUTE_LABELS[route]}类\n【实质技术响应主题】{_ROUTE_TOPICS[route]}",
    ]
    pairs: list[tuple[str, str]] = []
    devs: list[dict] = []
    missing_titles: list[str] = []
    generated_count = 0
    pro_call_count = 0
    async def generate_group(group):
        nonlocal pro_call_count
        pro_call_count += 1
        raw = await complete_json(client,
            [
                {
                    "role": "system",
                    "content": (
                        get_prompt("tech_plan_system", route_label=_ROUTE_LABELS[route], route_topics=_ROUTE_TOPICS[route])
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
        group_pairs = [(title, body) for title, body in group_pairs if '未完整生成' not in body]
        returned = {title for title, _ in group_pairs}
        for title in group:
            if title in returned:
                continue
            pro_call_count += 1
            retry = await complete_json(client,
                [{"role": "system", "content": get_prompt("tech_plan_system", route_label=_ROUTE_LABELS[route], route_topics=_ROUTE_TOPICS[route])},
                 {"role": "user", "content": "\n\n".join([*sections, "Return chapters JSON with a complete body for this existing chapter only: " + title])}],
                mode="pro",
            )
            repaired, extra_devs = _decode_payload(retry, [title])
            if not repaired or any('未完整生成' in body for _, body in repaired):
                raise AppError(ErrorCode.VALIDATION, f"技术章节「{title}」未返回有效正文，请重试生成", status_code=422)
            group_pairs.extend(repaired)
            group_devs.extend(extra_devs)
        return group_pairs, group_devs
    from api.services.tenders.generation_parallel import ordered_generation
    groups = [titles[start:start+2] for start in range(0, len(titles), 2)]
    results = await ordered_generation([lambda group=group: generate_group(group) for group in groups], stage='technical_plan', labels=[' / '.join(group) for group in groups])
    logger.info('generation stage=technical_plan pro_calls=%s chapters=%s', pro_call_count, len(titles))
    for group_pairs, group_devs in results:
        generated_count += len(group_pairs)
        pairs.extend(group_pairs)
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
    updated = [
        i.model_copy(update={"draftBody": drafted[i.title], "draftStatus": "drafted"})
        if i.title in drafted and i in editable
        else i
        for i in brief.outlineItems
    ]
    notes = [
        *source_notes,
        f"技术方案已填充 {generated_count} 个既有章节",
        brief.techScorecard,
    ]
    if missing_titles:
        notes.append(
            f"技术方案：{len(missing_titles)} 个章节正文未完整返回，"
            "已在原章节标【待补充】，投标文件继续生成"
        )
    return brief.model_copy(update={"outlineItems": updated, "deviationLines": deviations}), notes
