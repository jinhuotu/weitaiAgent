"""从投标邀请书/招标文件抽出项目侧字段，合并进表单。投标人信息不得被模型改写。"""

from __future__ import annotations

import json
import logging
import re
from typing import Any
from uuid import uuid4

from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from api.services.knowledge.classify import should_skip_kb_retrieval
from api.services.knowledge.ingest import search_knowledge_docs
from api.services.knowledge.parsers import assert_supported, extract_text_from_file, sniff_extension
from api.services.layouts.parse import extract_json_object
from api.services.models.runtime import build_llm_client
from api.services.tenders.assets import save_invitation_text, tender_invitations_dir
from api.services.tenders.quote import (
    attach_sheet,
    parse_quote_from_text,
    parse_quote_path,
)
from api.services.tenders.history import (
    BIDDER_LOCK,
    contains_foreign_bidder,
    latest_company_profile,
    load_history_playbooks,
    playbooks_prompt_block,
)
from api.services.tenders.placeholders import collect_slots, this_bid_keys
from api.services.tenders.schema import (
    COMPANY_FIELD_KEYS,
    BidBrief,
    DeviationLine,
    PerformanceLine,
    PlaceholderItem,
    default_brief,
)
from common.config import get_settings
from common.errors import AppError, ErrorCode

logger = logging.getLogger("api.tenders.extract")

_INVITE_MAX_CHARS = 18000
_KB_MAX_CHARS = 6000
_BIDDER_LOCK = BIDDER_LOCK

# 允许用邀请书覆盖的字段。投标人/法人/代理人一律不改。
_PATCHABLE = frozenset(
    {
        "projectName",
        "tenderer",
        "bidContent",
        "quality",
        "deliveryDays",
        "warrantyYears",
        "bidValidityDays",
        "bidPriceYuan",
        "prepaidPct",
        "arrivalPct",
        "settlementPct",
        "warrantyPct",
        "extraNote",
        "constructionPlan",
        "layoutPlan",
        "powerPlan",
        "omPlan",
        "schedulePlan",
        "techPlanNote",
    }
)
_INT_FIELDS = frozenset(
    {
        "deliveryDays",
        "warrantyYears",
        "bidValidityDays",
        "prepaidPct",
        "arrivalPct",
        "settlementPct",
        "warrantyPct",
    }
)

_SYSTEM = """你是投标文件助理。用户会上传甲方的投标邀请书或招标文件正文。
投标文件分商务标与技术标。商务标看有没有资格干（公司、资质、人员、业绩、报价、函件），缺资质或盖章错误会废标。
技术标看充电站打算怎么建好。实施方案由经办人上传本项目图纸并写文字说明，模型不要编造图纸和台数。
你的任务：抽出填「投标书表单」所需的项目侧字段，用 JSON 返回。
硬性规则：
1. 投标人永远是河南伟泰光电科技有限公司。不要把招标人、代理机构或其他公司写进投标人。
2. 找不到的字段填 null，不要编造报价、身份证、合同业绩、施工图纸或具体桩数。
3. 不要输出思考过程、Word 或 Markdown，只输出一个完整 JSON 对象。
4. 知识库摘录仅对照资格条款种类。出现其他公司投标文件、投标函或合同，一律忽略，不得当作伟泰资料。
5. 工程量清单必须来自本邀请书或用户上传的表格。没有表格则 quoteLines 必须为 []，禁止用其它项目的台数（例如 133/56）或历史投标书顶替。
6. 「历史组卷经验」只可用来建议 requiredMaterials 的 key 和技术标要不要写，禁止把其中任何项目名、报价、台数、合同业绩写入 JSON。
JSON 字段：
projectName, tenderer, bidContent, quality,
deliveryDays, warrantyYears, bidValidityDays, bidPriceYuan,
prepaidPct, arrivalPct, settlementPct, warrantyPct, extraNote,
quoteTitle, quoteTaxRate,
quoteLines（必须为 []；工程量由程序从本文件表格抽取，模型不要填）,
notes（字符串数组，给经办人看的提醒；商务缺项用「废标风险」，技术缺项用「扣分风险」）,
requiredMaterials（对象数组，每项 key/reason；key 必须来自用户提供的资料库清单。禁止编造 key，禁止把其他项目的扫描件、合同或台数当作本标附件。名称不同但同属一类的必须用已有 key，例如资料库「信用截图」对应邀请书「信用中国/政府采购网/国家企业信用信息公示查询截图」）,
missingMaterials（对象数组，每项 title/reason，仅当资料库完全没有同类项、邀请书额外要求时才填。不要因标题更长就新建；不要填其他项目的文件名；程序会建空项等用户上传）,
deviationLines（对象数组，每项 seq/requirement/response/deviation；requirement 必须来自本邀请书技术要求，禁止写死 7kW/30kW 充电桩套话。无条款则 []）,
performanceLines（必须为 []；伟泰合同业绩由资料库扫描件抽取，禁止把邀请书范例或其它公司合同写入）,
performanceRequirement（对象或 null：邀请书对类似业绩的资格门槛。字段 similarScope, minAmountYuan 单份最低金额元, minCount 至少几个, requireCompleted 是否须已竣工, keywords 字符串数组, note 原文短摘。邀请书没写则 null，禁止套用充电桩 20 万默认值）,
constructionPlan, layoutPlan, powerPlan, omPlan, schedulePlan（不要填；实施方案改为图纸+文字，由经办人上传）,
techPlanNote（中文字符串：仅当邀请书提出施工、布置、配电、运维或工期要求时，用两三句话概括伟泰拟响应的要点；没有则空字符串，禁止编造图纸、桩位和台数）,
factoryRole（字符串：如 充电设备生产厂商 / 供货单位 / 投标产品生产厂商；按本邀请书产品填写）。
数字字段用数字或 null；百分比用 0-100 的整数。quoteTaxRate 用 0.13 这种小数。"""


_TENDER_JSON_KEYS = (
    "projectName",
    "tenderer",
    "requiredMaterials",
    "bidContent",
    "quoteLines",
    "missingMaterials",
    "techPlanNote",
)


def parse_extract_payload(raw: str) -> dict[str, Any]:
    """从模型输出抽出 JSON。供测试与生成共用。"""
    try:
        return extract_json_object(raw, prefer_keys=_TENDER_JSON_KEYS)
    except AppError as exc:
        raise AppError(
            ErrorCode.VALIDATION,
            f"模型未能抽出结构化字段：{exc.msg}",
            status_code=422,
        ) from exc


def apply_extract_patch(base: BidBrief, patch: dict[str, Any]) -> tuple[BidBrief, list[str]]:
    """只合并项目侧字段；投标人/法人等保持 base 原值。"""
    data = base.model_dump()
    applied: list[str] = []
    for key in _PATCHABLE:
        if key not in patch:
            continue
        value = patch.get(key)
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if key in _INT_FIELDS:
            try:
                num = int(float(str(value).replace("%", "").strip()))
            except (TypeError, ValueError):
                continue
            if key in {"deliveryDays", "warrantyYears", "bidValidityDays"} and num < 1:
                continue
            if key.endswith("Pct") and not 0 <= num <= 100:
                continue
            data[key] = num
            applied.append(key)
            continue
        if key == "bidPriceYuan":
            try:
                num = float(str(value).replace(",", "").replace("元", "").strip())
            except (TypeError, ValueError):
                continue
            if num <= 0:
                continue
            data[key] = num
            applied.append(key)
            continue
        text = str(value).strip()
        if _looks_like_other_bidder(text, key):
            continue
        data[key] = text
        applied.append(key)
    data["bidderName"] = _BIDDER_LOCK if not (data.get("bidderName") or "").strip() else data["bidderName"]
    if _BIDDER_LOCK not in str(data.get("bidderName") or ""):
        data["bidderName"] = _BIDDER_LOCK
    deviations = deviation_lines_from_payload(patch)
    if deviations:
        data["deviationLines"] = [item.model_dump() for item in deviations]
        applied.append("deviationLines")
    perfs = performance_lines_from_payload(patch)
    if perfs:
        data["performanceLines"] = [item.model_dump() for item in perfs]
        applied.append("performanceLines")
    role = str(patch.get("factoryRole") or "").strip()
    if role and not _looks_like_other_bidder(role, "factoryRole"):
        data["factoryRole"] = role[:80]
        applied.append("factoryRole")
    if not str(data.get("techPlanNote") or "").strip():
        from api.services.tenders.categories import LEGACY_TECH_FIELDS

        joined = "\n\n".join(
            str(data.get(field) or "").strip() for field in LEGACY_TECH_FIELDS if str(data.get(field) or "").strip()
        )
        if joined:
            data["techPlanNote"] = joined
            applied.append("techPlanNote")
    return BidBrief.model_validate(data), applied


def fill_empty_company_fields(
    brief: BidBrief,
    *profiles: BidBrief | dict | None,
) -> tuple[BidBrief, list[str]]:
    """邀请书不含投标人资料。空的公司/法人字段用本公司默认值或上次投标回填。"""
    data = brief.model_dump()
    applied: list[str] = []
    sources: list[dict[str, Any]] = []
    for profile in profiles:
        if profile is None:
            continue
        if isinstance(profile, BidBrief):
            sources.append(profile.model_dump())
        elif isinstance(profile, dict):
            sources.append(profile)
    sources.append(default_brief().model_dump())
    for key in COMPANY_FIELD_KEYS:
        if str(data.get(key) or "").strip():
            continue
        for src in sources:
            value = src.get(key)
            if value is None:
                continue
            text = str(value).strip()
            if not text or _looks_like_other_bidder(text, key):
                continue
            data[key] = text
            applied.append(key)
            break
    data["bidderName"] = _BIDDER_LOCK
    if "bidderName" not in applied and not (brief.bidderName or "").strip():
        applied.append("bidderName")
    return BidBrief.model_validate(data), applied


def deviation_lines_from_payload(patch: dict[str, Any]) -> list[DeviationLine]:
    raw = patch.get("deviationLines")
    if not isinstance(raw, list):
        return []
    out: list[DeviationLine] = []
    for i, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            continue
        requirement = str(item.get("requirement") or item.get("req") or "").strip()
        if not requirement:
            continue
        response = str(item.get("response") or item.get("reply") or "").strip() or requirement
        deviation = str(item.get("deviation") or "无偏差").strip() or "无偏差"
        seq = str(item.get("seq") or i).strip() or str(i)
        out.append(
            DeviationLine(seq=seq, requirement=requirement, response=response, deviation=deviation)
        )
    return out[:20]


def performance_lines_from_payload(patch: dict[str, Any]) -> list[PerformanceLine]:
    from api.services.tenders.performance import performance_from_dict, rank_performance_lines

    raw = patch.get("performanceLines")
    if not isinstance(raw, list):
        return []
    out: list[PerformanceLine] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        line = performance_from_dict(item, allow_client_title=False)
        if line is None:
            continue
        out.append(line)
    return rank_performance_lines(out)[:8]


def merge_performance_lines(
    primary: list[PerformanceLine],
    extra: list[PerformanceLine],
    requirement=None,
    *,
    preserve_flags: bool = False,
) -> list[PerformanceLine]:
    from api.services.tenders.performance import (
        apply_include_in_bid,
        is_weak_title,
        line_name_key,
        rank_performance_lines,
    )

    out: list[PerformanceLine] = []
    seen: set[str] = set()
    preserve: dict[str, bool] = {}
    for item in list(primary or []) + list(extra or []):
        name = (item.projectName or "").strip()
        if not name or is_weak_title(name):
            continue
        key = line_name_key(name)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    if preserve_flags:
        for item in primary or []:
            name = (item.projectName or "").strip()
            if not name or is_weak_title(name):
                continue
            preserve[line_name_key(name)] = bool(getattr(item, "includeInBid", True))
    ranked = rank_performance_lines(out, requirement=requirement)[:8]
    return apply_include_in_bid(ranked, requirement, preserve=preserve if preserve_flags else None)


def _line_field(line: object, key: str) -> str:
    if isinstance(line, dict):
        return str(line.get(key) or "").strip()
    return str(getattr(line, key, None) or "").strip()


def deviation_lines_from_quote(lines: list) -> list[DeviationLine]:
    out: list[DeviationLine] = []
    for i, line in enumerate(lines or [], start=1):
        name = _line_field(line, "name")
        spec = _line_field(line, "spec")
        if not name and not spec:
            continue
        if name and spec:
            requirement = f"{name}\n{spec}"
            response = (
                f"我司所投{name}：\n{spec}\n"
                "含供货、安装、调试，安装费已含在综合单价内。"
            )
        else:
            requirement = name or spec
            response = f"我司所投{name or '产品'}：{spec or name}，含供货、安装、调试，安装费已含在综合单价内。"
        out.append(
            DeviationLine(seq=str(i), requirement=requirement, response=response, deviation="无偏差")
        )
    return out


def slots_from_payload(
    patch: dict[str, Any],
    *,
    catalog: list[PlaceholderItem] | None = None,
) -> list[PlaceholderItem]:
    from api.services.tenders.match import slots_from_materials, split_invitation_materials

    mapped, missing = split_invitation_materials(patch, catalog)
    return slots_from_materials(mapped, missing)


def notes_from_payload(patch: dict[str, Any]) -> list[str]:
    raw = patch.get("notes")
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        text = str(item or "").strip()
        if text:
            out.append(text)
    return out


async def parse_invitation(
    db: AsyncSession,
    upload: UploadFile,
    *,
    kb_ids: list[str] | None = None,
    current: BidBrief | None = None,
    quote_upload: UploadFile | None = None,
    created_by: int | None = None,
) -> dict[str, Any]:
    path = await _save_upload(upload)
    quote_path = None
    if quote_upload is not None and (quote_upload.filename or "").strip():
        quote_path = await _save_upload(quote_upload)
    extracted = await extract_text_from_file(path, ext=path.suffix.lower().lstrip("."))
    invitation = (extracted.text or "").strip()
    invitation_id = save_invitation_text(invitation, stem=path.stem)
    settings = get_settings()
    max_ocr = max(0, int(settings.ocr_max_pages))
    if len(invitation) < 20:
        hint = "邀请书解析正文过短，请换可检索的 PDF/Word"
        if extracted.ocr_capped:
            hint += f"（已达 OCR 上限 {max_ocr} 页，可提高 OCR_MAX_PAGES 后重试）"
        elif path.suffix.lower().lstrip(".") in {
            "pdf",
            "png",
            "jpg",
            "jpeg",
            "webp",
            "bmp",
            "tif",
            "tiff",
        } and not extracted.ocr_pages:
            hint += "。若为扫描件，请确认已配置 OCR"
        raise AppError(ErrorCode.VALIDATION, hint, status_code=422)

    kb_hits, kb_notes = await _recall_knowledge(db, invitation, kb_ids or [])
    if contains_foreign_bidder(invitation):
        kb_notes = [
            "正文出现其他公司名称，请确认上传的是甲方邀请书/招标文件，而不是他人已填的投标书",
            *kb_notes,
        ]
    playbooks = await load_history_playbooks(db)
    from api.services.tenders.library_kb import (
        catalog_placeholders,
        list_library_items,
        performance_from_library,
        persist_parsed_materials,
    )

    catalog = await catalog_placeholders(db)
    catalog_rows = await list_library_items(db)
    client = await build_llm_client(db, "fast")
    messages = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": _user_prompt(invitation, kb_hits, catalog_rows, playbooks)},
    ]
    raw = await client.complete(messages, mode="fast")
    try:
        patch = parse_extract_payload(raw)
    except AppError as first:
        if first.status_code != 422:
            raise
        logger.warning(
            "tender extract json retry chars=%s err=%s head=%s",
            len(raw or ""),
            first.msg,
            (raw or "")[:240],
        )
        raw = await client.complete(
            [
                *messages,
                {"role": "assistant", "content": (raw or "")[:6000]},
                {
                    "role": "user",
                    "content": (
                        "上一轮无法解析为 JSON。请只输出一个完整 JSON 对象，"
                        "不要思考过程、不要 markdown、不要解释。"
                    ),
                },
            ],
            mode="fast",
        )
        try:
            patch = parse_extract_payload(raw)
        except AppError as second:
            raise AppError(
                ErrorCode.VALIDATION,
                (
                    f"{second.msg}。"
                    "DeepSeek-R1 等推理模型常把思考写进正文或截断 JSON，"
                    "请改用 DeepSeek-V3 / Qwen 等非思考对话模型再识别。"
                ),
                status_code=422,
            ) from second
    brief, filled = apply_extract_patch(current or default_brief(), patch)
    brief.invitationId = invitation_id
    company_profile = await latest_company_profile(db)
    brief, company_filled = fill_empty_company_fields(brief, current, company_profile)
    for key in company_filled:
        if key not in filled:
            filled.append(key)
    extras = slots_from_payload(patch, catalog=catalog)
    before_keys = {item.key for item in catalog if item.key}
    extras = await persist_parsed_materials(db, extras, created_by=created_by)
    created_keys = [item.key for item in extras if item.key and item.key not in before_keys]
    catalog = await catalog_placeholders(db)
    has_agent = bool((brief.agentName or "").strip() or (brief.agentIdNo or "").strip())
    required_keys, include_keys = this_bid_keys(
        extra=extras,
        catalog=catalog,
        required_keys=[item.key for item in extras],
        include_keys=None,
        has_agent=has_agent,
    )
    from api.services.tenders.format_rules import (
        extract_document_format,
        extract_tender_no,
        format_brief_notes,
    )
    from api.services.tenders.outline import (
        apply_auth_outline,
        choose_layout_mode,
        extract_auth_need,
        extract_outline,
    )
    from api.services.tenders.performance import (
        extract_performance_requirement,
        format_requirement,
        merge_performance_requirement,
        performance_match_issues,
        requirement_from_payload,
    )

    chapter, outline_items = extract_outline(invitation)
    brief.authNeed = extract_auth_need(invitation, outline_items)
    outline_items = apply_auth_outline(
        outline_items,
        has_agent=has_agent,
        auth_need=brief.authNeed,
    )
    brief.layoutMode = choose_layout_mode(chapter, outline_items)
    brief.outlineChapter = chapter
    brief.outlineItems = outline_items
    brief.documentFormat = extract_document_format(invitation)
    tender_no = extract_tender_no(invitation)
    if tender_no:
        brief.tenderNo = tender_no
    brief.performanceRequirement = merge_performance_requirement(
        extract_performance_requirement(invitation),
        requirement_from_payload(patch),
        invitation=invitation,
    )
    brief.extraPlaceholders = extras
    brief.requiredSlotKeys = required_keys
    brief.includeSlotKeys = include_keys
    brief.includePlaceholders = True

    quote_note, brief, quote_filled = _merge_quote(brief, patch, invitation, path, quote_path)
    if quote_filled:
        filled = [*filled, "quoteLines"]
        if "bidPriceYuan" not in filled and brief.bidPriceYuan > 0:
            filled.append("bidPriceYuan")
    from api.services.tenders.tables import apply_format_table_headers

    header_notes = apply_format_table_headers(brief)
    if not brief.deviationLines:
        from_quote = deviation_lines_from_quote(brief.quoteLines)
        if from_quote:
            brief.deviationLines = from_quote
            filled = [*filled, "deviationLines"]
    lib_perf = await performance_from_library(db, extract_missing=False)
    if lib_perf or brief.performanceLines:
        brief.performanceLines = merge_performance_lines(
            brief.performanceLines,
            lib_perf,
            requirement=brief.performanceRequirement,
        )
        if brief.performanceLines and "performanceLines" not in filled:
            filled = [*filled, "performanceLines"]

    slots = collect_slots(extras, catalog=catalog, include_keys=include_keys)
    catalog_rows = await list_library_items(db)
    from api.services.tenders.match import attachment_match_notes, build_attachment_match

    attachment_match = build_attachment_match(
        catalog_rows,
        required_keys=required_keys,
        include_keys=include_keys,
        created_keys=created_keys,
    )
    notes = [
        *kb_notes,
        *notes_from_payload(patch),
        *quote_note,
        *header_notes,
    ]
    req_label = format_requirement(brief.performanceRequirement)
    if lib_perf:
        selected = sum(1 for row in brief.performanceLines if getattr(row, "includeInBid", True))
        if req_label:
            notes.append(
                f"类似业绩已从资料库合同/发票识别，已按招标门槛预选 {selected} 条写入本标；"
                "不符项仍在表中可勾选。资料库原件未改。"
            )
        else:
            notes.append("类似业绩已从资料库合同/发票识别；未抽出本标门槛，表中业绩均列入本标，可自行勾选。")
    if req_label:
        notes.append(f"招标业绩要求：{req_label}")
        notes.extend(performance_match_issues(brief.performanceLines, brief.performanceRequirement))
    else:
        notes.append("未从招标书抽出类似业绩门槛（同类/金额/数量），表中业绩仅供核对，未按本标筛选")
    if extracted.ocr_capped:
        notes.insert(
            0,
            f"邀请书共 {extracted.page_count or '?'} 页，OCR 已达上限 {max_ocr} 页"
            f"（实际识别 {extracted.ocr_pages} 页）。未识别页未进入抽取，可提高 OCR_MAX_PAGES 后重试。",
        )
    elif extracted.ocr_pages:
        notes.insert(0, f"邀请书已 OCR {extracted.ocr_pages} 页（共 {extracted.page_count or extracted.ocr_pages} 页）")
    if not filled:
        notes.append("模型未抽出可写入表单的字段，请核对邀请书是否为可选中的文字稿，并手工填写")
    else:
        notes.insert(0, "已根据邀请书回填：" + "、".join(_label(k) for k in filled))
    notes.extend(attachment_match_notes(attachment_match))
    if outline_items:
        copied = sum(1 for item in outline_items if (item.body or "").strip())
        if brief.layoutMode == "outline":
            notes.append(
                f"已抽出组卷大纲 {len(outline_items)} 条"
                + (f"（{chapter}）" if chapter else "")
                + f"，将按大纲组卷；已复制空白稿 {copied} 节，承诺函只填单位和日期"
            )
        else:
            notes.append(
                f"已抽出组卷大纲 {len(outline_items)} 条"
                + (f"（{chapter}）" if chapter else "")
                + "，本标仍用公司固定模板；可在组卷大纲中改为按本标招标书组卷"
            )
    else:
        notes.append("未从招标书抽出「投标/响应文件格式」章节，生成仍用公司固定模板")
    if brief.authNeed == "required":
        notes.append("招标书要求提供授权委托书，请填写委托代理人并上传身份证")
    elif brief.authNeed == "optional":
        if has_agent:
            notes.append("已填委托代理人，授权委托书将写入并贴身份证")
        else:
            notes.append("招标书有授权委托书格式；未填委托人则默认跳过，法人自签即可")
    notes.extend(format_brief_notes(brief.documentFormat))

    preview = invitation[:1200] + ("…" if len(invitation) > 1200 else "")
    return {
        "brief": brief.model_dump(),
        "filledKeys": filled,
        "notes": notes,
        "placeholders": [item.model_dump() for item in slots],
        "requiredSlotKeys": required_keys,
        "includeSlotKeys": include_keys,
        "attachmentMatch": attachment_match,
        "fileName": upload.filename or path.name,
        "quoteFileName": (quote_upload.filename if quote_upload is not None else None),
        "charCount": len(invitation),
        "pageCount": int(extracted.page_count or 0),
        "ocrPages": int(extracted.ocr_pages or 0),
        "ocrCapped": bool(extracted.ocr_capped),
        "ocrMaxPages": max_ocr,
        "preview": preview,
        "knowledgeHits": kb_hits,
    }


def _label(key: str) -> str:
    return {
        "projectName": "项目名称",
        "tenderer": "招标人",
        "bidContent": "投标内容",
        "quality": "质量",
        "deliveryDays": "供货期",
        "warrantyYears": "质保期",
        "bidValidityDays": "投标有效期",
        "bidPriceYuan": "投标总价",
        "prepaidPct": "预付款",
        "arrivalPct": "到货款",
        "settlementPct": "结算款",
        "warrantyPct": "质保金",
        "extraNote": "需要说明的问题",
        "quoteLines": "分项工程量",
        "deviationLines": "技术偏差",
        "performanceLines": "类似业绩",
        "performanceRequirement": "业绩门槛",
        "factoryRole": "原厂角色",
        "constructionPlan": "施工方案",
        "layoutPlan": "平面布置",
        "powerPlan": "配电方案",
        "omPlan": "运维方案",
        "schedulePlan": "工期安排",
        "techPlanNote": "实施方案说明",
        "bidderName": "投标人全称",
        "bidderAddress": "地址",
        "bidderPhone": "电话",
        "bidderEmail": "邮箱",
        "foundedDate": "成立日期",
        "legalPersonName": "法人姓名",
        "legalPersonAge": "年龄",
        "legalPersonIdNo": "法人身份证号",
        "legalPersonTitle": "职务",
    }.get(key, key)


def _looks_like_other_bidder(text: str, key: str) -> bool:
    if key != "tenderer" and contains_foreign_bidder(text):
        return True
    return False


def _user_prompt(
    invitation: str,
    kb_hits: list[dict[str, Any]],
    catalog_rows: list[dict[str, Any]] | None = None,
    playbooks: list | None = None,
) -> str:
    body = invitation[:_INVITE_MAX_CHARS]
    parts = [
        f"投标人（不得修改）：{_BIDDER_LOCK}",
        "请阅读邀请书正文并抽出 JSON。",
        "",
        "【邀请书正文】",
        body,
    ]
    if catalog_rows:
        from api.services.tenders.library_kb import catalog_prompt_lines

        parts.append("")
        parts.append("【投标资料库清单·requiredMaterials 的 key 必须从此清单选取】")
        parts.append(catalog_prompt_lines(catalog_rows))
    history_block = playbooks_prompt_block(playbooks)
    if history_block:
        parts.append("")
        parts.append(history_block)
    safe_hits = [
        hit
        for hit in (kb_hits or [])
        if not should_skip_kb_retrieval(
            name=str(hit.get("name") or ""),
            content=str(hit.get("content") or ""),
            tags=hit.get("tags") if isinstance(hit.get("tags"), list) else None,
        )
    ]
    if safe_hits:
        parts.append("")
        parts.append("【知识库摘录·仅对照资格条款，禁止套用历史投标书或他司合同】")
        for hit in safe_hits:
            name = str(hit.get("name") or "资料")
            content = str(hit.get("content") or "").strip()[:1800]
            if content:
                parts.append(f"—— {name} ——\n{content}")
    return "\n".join(parts)


async def _recall_knowledge(
    db: AsyncSession,
    invitation: str,
    kb_ids: list[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    notes: list[str] = []
    ids = [x.strip() for x in kb_ids if str(x).strip()]
    if not ids:
        return [], notes
    query = " ".join(invitation.split())[:240] + " 投标资格 保证金 交货期 付款比例 类似业绩"
    try:
        docs = await search_knowledge_docs(db, query=query, top_k=3, kb_ids=ids, max_drawings=0)
    except AppError as exc:
        notes.append(f"知识库未检索：{exc.msg}。已仅根据邀请书抽取")
        return [], notes
    except Exception as exc:  # noqa: BLE001
        logger.warning("tender kb search failed: %s", exc)
        detail = str(exc)
        if "unauthorized" in detail.lower() or "401" in detail:
            notes.append("知识库检索鉴权失败（Embedding 或向量库密钥）。已仅根据邀请书抽取")
        else:
            notes.append("知识库检索失败，已仅根据邀请书抽取")
        return [], notes

    kept: list[dict[str, Any]] = []
    skipped = 0
    used = 0
    for doc in docs:
        content = str(doc.get("content") or "")
        if should_skip_kb_retrieval(
            name=str(doc.get("name") or ""),
            content=content,
            tags=doc.get("tags") if isinstance(doc.get("tags"), list) else None,
        ):
            skipped += 1
            continue
        clip = content[:2000]
        used += len(clip)
        if used > _KB_MAX_CHARS:
            break
        kept.append({"name": doc.get("name"), "docId": doc.get("doc_id"), "content": clip})
    if skipped:
        notes.append(f"已忽略 {skipped} 篇疑似投标书或其他公司材料的知识库文档")
    if kept:
        notes.append("已结合知识库 " + "、".join(str(x.get("name") or "资料") for x in kept))
    elif ids:
        notes.append("所选知识库没有可用摘录，已仅根据邀请书抽取")
    return kept, notes


def _merge_quote(
    brief: BidBrief,
    patch: dict[str, Any],
    invitation: str,
    invite_path,
    quote_path,
) -> tuple[list[str], BidBrief, bool]:
    del patch
    notes: list[str] = []
    sheet = parse_quote_path(quote_path) if quote_path is not None else None
    source = "file" if sheet is not None else ""
    if sheet is None and quote_path is not None:
        notes.append("工程量清单文件未能按表格解析，已改从邀请书里找")
    if sheet is None:
        sheet = parse_quote_path(invite_path)
        if sheet is not None:
            source = "file"
    if sheet is None:
        sheet = parse_quote_from_text(invitation, title_hint=brief.projectName)
        if sheet is not None:
            source = "text"
    if sheet is None:
        notes.append(
            "未解析到工程量清单。请另传 Excel/Word 清单后重新识别再生成。"
            "未采用模型或历史投标书中的台数。"
        )
        if brief.quoteLines:
            data = brief.model_dump()
            data["quoteLines"] = []
            data["quoteSource"] = ""
            data["quoteSourceIncTax"] = 0
            brief = BidBrief.model_validate(data)
            notes.append("已清空上次表单里的分项，避免沿用其他项目工程量")
        return notes, brief, False
    before = float(brief.bidPriceYuan or 0)
    brief = attach_sheet(brief, sheet, source=source)
    notes.append(f"已解析分项 {len(sheet.lines)} 项（《{sheet.title}》，清单含税 {sheet.total_inc_tax}）")
    if before <= 0 and brief.bidPriceYuan > 0:
        notes.append("投标总价已按清单含税合计填入，可按二次报价再改")
    return notes, brief, True


async def _save_upload(upload: UploadFile):
    settings = get_settings()
    filename = (upload.filename or "invitation.bin").replace("\\", "/").split("/")[-1]
    ext = assert_supported(sniff_extension(filename, upload.content_type))
    dest_dir = tender_invitations_dir()
    dest = dest_dir / f"{uuid4().hex[:12]}.{ext}"
    max_bytes = int(settings.kb_upload_max_bytes)
    size = 0
    try:
        with dest.open("wb") as fh:
            while True:
                chunk = await upload.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise AppError(
                        ErrorCode.VALIDATION,
                        f"文件超过大小上限（{max_bytes} 字节）",
                        status_code=422,
                    )
                fh.write(chunk)
    except Exception:
        dest.unlink(missing_ok=True)
        raise
    if size <= 0:
        dest.unlink(missing_ok=True)
        raise AppError(ErrorCode.VALIDATION, "上传文件为空", status_code=422)
    return dest


def parse_kb_ids(raw: str | None) -> list[str]:
    if raw is None or not str(raw).strip():
        return []
    text = str(raw).strip()
    if text.startswith("["):
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise AppError(ErrorCode.VALIDATION, "kbIds 必须是 JSON 数组或逗号分隔", status_code=422) from exc
        if not isinstance(data, list):
            raise AppError(ErrorCode.VALIDATION, "kbIds 必须是 JSON 数组", status_code=422)
        return [str(x).strip() for x in data if str(x).strip()]
    return [p.strip() for p in text.split(",") if p.strip()]
