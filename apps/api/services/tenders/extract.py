"""从投标邀请书/招标文件抽出项目侧字段，合并进表单。投标人信息不得被模型改写。"""

from __future__ import annotations

from api.services.tenders.prompts import get_prompt

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from api.services.knowledge.classify import should_skip_kb_retrieval
from api.services.knowledge.ingest import search_knowledge_docs
from api.services.knowledge.parsers import assert_supported, extract_text_from_file, sniff_extension
from api.services.layouts.parse import extract_json_object
from api.services.models.runtime import build_llm_client
from api.services.word_convert import convert_doc_to_docx
from api.services.tenders.assets import save_invitation_text, tender_invitations_dir
from api.services.tenders.quote import (
    attach_sheet,
    parse_quote_path,
    sheet_from_quote_records,
)
from api.services.tenders.history import (
    contains_foreign_bidder,
    latest_agent_profile,
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
_KB_QUAL_TAIL = " 投标资格 保证金 交货期 付款比例 类似业绩"
_KB_FORMAT_QUERY = "投标文件格式 响应文件格式 响应函格式 报价表 偏离表 目录 装订"

# 允许用邀请书覆盖的字段。投标人/法人/代理人一律不改。
_PATCHABLE = frozenset(
    {
        "projectName",
        "tenderer",
        "tenderNo",
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
        "controlPriceYuan",
        "safetyFeeYuan",
        "provisionalYuan",
        "primeCostYuan",
        "bidBondYuan",
        "pmName",
        "pmCertNo",
        "pmGrade",
        "defectPeriod",
        "perfBond",
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
_MONEY_FIELDS = frozenset(
    {
        "bidPriceYuan",
        "controlPriceYuan",
        "safetyFeeYuan",
        "provisionalYuan",
        "primeCostYuan",
        "bidBondYuan",
    }
)

_SYSTEM = get_prompt("extract_system")


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
        payload = extract_json_object(raw, prefer_keys=_TENDER_JSON_KEYS)
        for key in ('requiredMaterials', 'missingMaterials', 'deviationLines', 'performanceLines', 'quoteLines', 'materialIntents'):
            if isinstance(payload.get(key), dict):
                payload[key] = [payload[key]]
        return payload
    except AppError as exc:
        # Enhanced error reporting for debugging
        preview = raw[:500] if len(raw) <= 500 else raw[:250] + "..." + raw[-250:]
        logger.warning(
            "parse_extract_payload failed: %s preview=%s",
            str(exc)[:200],
            preview,
        )
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
        if key == "tenderNo" and not _missing_project_fact(data.get(key)):
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
        if key in _MONEY_FIELDS:
            try:
                num = float(str(value).replace(",", "").replace("元", "").strip())
            except (TypeError, ValueError):
                continue
            if key == "bidPriceYuan" and num <= 0:
                continue
            if num < 0:
                continue
            data[key] = num
            applied.append(key)
            continue
        text = str(value).strip()
        if key in {"projectName", "tenderer", "tenderNo"} and _missing_project_fact(text):
            continue
        if _looks_like_other_bidder(text, key):
            continue
        data[key] = text
        applied.append(key)
    data["bidderName"] = (base.bidderName or "").strip()
    from api.services.tenders.outline import _clean_pm_name

    data["pmName"] = _clean_pm_name(str(data.get("pmName") or ""))
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
    """空的公司/法人字段只从已选同一公司的资料回填，缺失保持为空。"""
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
    from api.services.tenders.company_identity import same_company
    selected = (brief.bidderName or "").strip()
    if not selected:
        selected = next((str(p.get("bidderName") or "").strip() for p in sources if p.get("bidderName")), "")
    sources = [p for p in sources if same_company(p.get("bidderName"), selected)]
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
    data["bidderName"] = selected
    if selected and "bidderName" not in applied and not (brief.bidderName or "").strip():
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
        response = str(item.get("response") or item.get("reply") or "").strip()
        deviation = str(item.get("deviation") or "").strip()
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
        else:
            requirement = name or spec
        out.append(
            DeviationLine(
                seq=str(i),
                requirement=requirement,
                response="【待响应】",
                deviation="待确认",
            )
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


_YUAN_NUM = r"([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{1,2})?|[0-9]+(?:\.[0-9]{1,2})?)"


def apply_invite_letter_fields(
    brief: BidBrief, invitation: str, *, file_name: str = "",
) -> BidBrief:
    """从招标书正文抽工期/有效期/控制价栏目，各标通用，不改投标人。"""
    text = invitation or ""
    data = brief.model_dump()

    def _days(pat: str) -> int | None:
        hit = re.search(pat, text)
        if not hit:
            return None
        n = int(hit.group(1))
        return n if 1 <= n <= 3650 else None

    duration = _days(r"(?:计划工期|施工工期|工期|交货期)[^\d]{0,8}(\d{1,4})\s*(?:日历天|天)")
    if duration:
        data["deliveryDays"] = duration
    valid = _days(r"投标有效期[^\d]{0,8}(\d{1,4})\s*(?:日历天|天)")
    if valid:
        cur = int(data.get("bidValidityDays") or 0)
        data["bidValidityDays"] = max(cur, valid) if cur else valid
    q = re.search(r"质量(?:要求|标准)[：:为是]?\s*([合格优良优质][^\s，。；;]{0,10})", text)
    if q:
        data["quality"] = q.group(1).strip()

    def _yuan(*labels: str) -> float | None:
        for lab in labels:
            m = re.search(
                lab + r"[^\d万¥￥]{0,16}(?:人民币)?[¥￥]?\s*" + _YUAN_NUM + r"\s*元",
                text,
            )
            if m:
                return float(m.group(1).replace(",", ""))
            m2 = re.search(lab + r"[^\d]{0,12}(\d+(?:\.\d+)?)\s*万元", text)
            if m2:
                return float(m2.group(1)) * 10000
        return None

    mapping = (
        ("controlPriceYuan", ("招标控制价", "最高投标限价")),
        ("safetyFeeYuan", ("安全生产措施费", "安全文明施工费")),
        ("provisionalYuan", ("暂列金额",)),
        ("primeCostYuan", ("专业工程暂估价",)),
        ("bidBondYuan", ("投标保证金",)),
    )
    for key, labels in mapping:
        if data.get(key) is not None:
            continue
        val = _yuan(*labels)
        if val is not None and val >= 0:
            data[key] = val
    pm = re.search(r"项目经理[：:]\s*([\u4e00-\u9fff]{2,4})", text)
    if pm and not str(data.get("pmName") or "").strip():
        from api.services.tenders.outline import _clean_pm_name

        name = _clean_pm_name(pm.group(1))
        if name:
            data["pmName"] = name
    cert = re.search(r"(?:注册证号|证书编号)[：:]\s*([A-Za-z0-9\-_]{6,32})", text)
    if cert and not str(data.get("pmCertNo") or "").strip():
        data["pmCertNo"] = cert.group(1)
    _fill_project_facts(data, text, file_name=file_name)
    defect = re.search(r"缺陷责任期[^\d]{0,10}(\d{1,3})\s*(个月|月|年|日历天)", text)
    if defect and not str(data.get("defectPeriod") or "").strip():
        data["defectPeriod"] = defect.group(1) + defect.group(2)
    pct = re.search(r"履约担保(?:金|金额)?[^\d%]{0,16}(\d+(?:\.\d+)?)\s*%", text)
    if pct and not str(data.get("perfBond") or "").strip():
        data["perfBond"] = f"中标合同价的{pct.group(1)}%"
    elif not str(data.get("perfBond") or "").strip():
        bond = _yuan("履约担保金额", "履约保证金")
        if bond is not None and bond >= 0:
            from api.services.tenders.money import rmb_pair

            data["perfBond"] = rmb_pair(bond)
    return BidBrief.model_validate(data)


_PROJECT_NAME = re.compile(r"(?:招标)?项目名称\s*[：:]\s*([^\n；;]{4,80})")
_TENDERER = re.compile(
    r"招标人(?:名称)?\s*[：:为]\s*"
    r"([\u4e00-\u9fffA-Za-z0-9（）()·]{4,40}?(?:公司|局|中心|委员会|集团|大学|医院))"
)
_BID_SCOPE = re.compile(
    r"招标范围\s*[：:]\s*(.+?)(?=\s*2\.\d|\s*1\.\d\.\d|\s*计划工期|\s*合同估算价|\Z)",
    re.S,
)


_FACT_LABELS = frozenset({
    "项目名称", "采购项目名称", "招标项目名称", "工程名称", "采购人", "采购人名称",
    "招标人", "招标人名称", "采购单位", "采购单位名称", "询价人", "询价单位",
    "项目单位", "招标编号", "采购编号", "询价编号", "项目编号", "待填写", "待填",
})
_DOCUMENT_TITLE_END = re.compile(
    r"(?:采购询价(?:文件|函)|询价采购文件|采购文件|采购公告|询价文件|询价函|询价公告|"
    r"招标文件|招标公告|竞争性谈判文件|竞争性磋商文件|比选文件)"
    r"(?:[（(](?:发出版|发布版|定稿版|最终版|修订版)[^（）()\n]{0,40}[)）])?\s*$"
)


def _missing_project_fact(value: Any) -> bool:
    raw = re.sub(r"\s+", "", str(value or ""))
    if not raw or raw.strip("（）()【】[]《》<>：:_—-….") in _FACT_LABELS:
        return True
    if re.fullmatch(r"[_—\-….]+|[xX×]{2,}", raw):
        return True
    return bool(re.search(
        r"[（(【\[]\s*(?:项目名称|采购项目名称|采购人名称|招标人名称|采购人|招标人|"
        r"招标编号|采购编号|询价编号)\s*[)）】\]]", raw,
    ))


def _project_name_candidate(value: str) -> str:
    name = re.sub(r"\s+", "", value).strip("，,。；;《》\"“”")
    if len(name) < 6 or len(name) > 400 or _missing_project_fact(name):
        return ""
    if any(word in name for word in ("由招标人", "示范文本", "请填写", "以下简称", "详见附件")):
        return ""
    if re.match(r"^(?:采购人|招标人|采购单位|询价单位|项目单位|采购编号|招标编号)[:：|]", name):
        return ""
    if name in {"项目名称", "建设项目", "采购项目", "采购询价文件", "招标文件", "订货技术要求"}:
        return ""
    return name


def _project_from_title(value: str) -> str:
    name = _DOCUMENT_TITLE_END.sub("", value.strip()).strip(" -_—：:")
    name = _project_name_candidate(name)
    return name if re.search(r"项目|工程|采购|系统|设备|建设", name) else ""


def _project_from_cover(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()][:60]
    for index, line in enumerate(lines):
        if re.match(r"^(?:第[一二三四五六七八九十0-9]+[章部分]|总目录|目录$)", line):
            break
        if not _DOCUMENT_TITLE_END.search(line):
            continue
        inline = _project_from_title(line)
        if inline:
            return inline
        # 封面常把项目名、包号和“采购询价函”写成相邻的三个段落。
        for start in range(index - 1, max(-1, index - 6), -1):
            if (
                not re.search(r"项目|工程", lines[start])
                or not _project_name_candidate(lines[start])
            ):
                continue
            if re.search(r"[：:|。；;]|招标人|采购人|项目单位", lines[start]):
                continue
            pieces = [lines[start]]
            for tail in lines[start + 1:index]:
                if re.match(r"^(?:包[一二三四五六七八九十百0-9]+|第.+包|标段)", tail):
                    pieces.append(tail)
            candidate = _project_from_title("".join(pieces))
            if candidate:
                return candidate
    return ""


def _project_from_attachment(text: str) -> str:
    candidates = set()
    for match in re.finditer(
        r"《([^》\n]{6,400}?)(?:订货技术要求|采购技术要求|技术规格书)》", text,
    ):
        context = text[max(0, match.start() - 50):match.start()]
        if not re.search(r"附件|详见|祥见|本项目|本次采购", context):
            continue
        if re.search(r"类似业绩|历史项目|往年项目", context):
            continue
        candidate = _project_from_title(match.group(1))
        if candidate:
            candidates.add(candidate)
    return next(iter(candidates)) if len(candidates) == 1 else ""


def _project_from_filename(file_name: str) -> str:
    name = Path(file_name.replace("\\", "/")).name
    if Path(name).suffix.lower() in {".doc", ".docx"}:
        name = name[: -len(Path(name).suffix)]
    # 无项目语义的随机落盘 ID 和通用“询价文件”不能当项目名称。
    if not _DOCUMENT_TITLE_END.search(name):
        return ""
    return _project_from_title(name)


def _fill_project_facts(data: dict, text: str, *, file_name: str = "") -> None:
    """模型没抽出时，从招标公告和投标人须知里补项目名称、招标人、编号、招标范围。"""
    for key in ("projectName", "tenderer", "tenderNo"):
        if _missing_project_fact(data.get(key)):
            data[key] = ""
    if not data.get("projectName"):
        for hit in _PROJECT_NAME.finditer(text or ""):
            if text[max(0, hit.start() - 2):hit.start()] in {"采购", "询价"}:
                continue
            name = re.sub(r"\s+", "", hit.group(1)).strip("，,。")
            if not _project_name_candidate(name) or "示范" in name or "由招标人" in name:
                continue
            data["projectName"] = name
            break
    if not data.get("projectName"):
        for hit in re.finditer(
            r"(?:采购项目名称|询价项目名称|采购名称|询价名称)\s*[:：|]\s*([^\n；;|]{6,400})", text,
        ):
            name = _project_name_candidate(hit.group(1))
            if name:
                data["projectName"] = name
                break
    if not data.get("projectName"):
        data["projectName"] = (
            _project_from_cover(text) or _project_from_attachment(text)
            or _project_from_filename(file_name)
        )
    if not str(data.get("tenderer") or "").strip():
        for hit in _TENDERER.finditer(text or ""):
            if not _missing_project_fact(hit.group(1)):
                data["tenderer"] = re.sub(r"\s+", "", hit.group(1))
                break
    if not data.get("tenderer"):
        labels = (
            r"采购人(?:名称)?|采购单位(?:名称)?|询价人(?:名称)?|询价单位(?:名称)?|"
            r"项目单位[（(](?:即)?(?:采购人|询价人)[)）]"
        )
        for hit in re.finditer(rf"(?:{labels})\s*[:：|]\s*([^\n；;|]{{4,160}})", text):
            org = re.split(
                r"\s+(?:地址|联系人|联系电话|采购编号|询价编号)\s*[:：]", hit.group(1),
            )[0]
            org = re.sub(r"[（(](?:盖章|盖单位章)[)）].*$", "", org)
            org = re.sub(r"\s+", "", org).strip("，,。")
            if not _missing_project_fact(org):
                data["tenderer"] = org
                break
    if not str(data.get("tenderNo") or "").strip():
        from api.services.tenders.format_rules import extract_tender_no

        no = extract_tender_no(text or "")
        if no and not _missing_project_fact(no):
            data["tenderNo"] = no
        else:
            for hit in re.finditer(
                r"(?:询价(?:文件|项目)?编号|采购项目编号)\s*[:：为|]?\s*"
                r"([A-Za-z0-9][A-Za-z0-9\-_/]{2,63})", text,
            ):
                if not _missing_project_fact(hit.group(1)):
                    data["tenderNo"] = hit.group(1)
                    break
    if not str(data.get("bidContent") or "").strip():
        hit = _BID_SCOPE.search(text or "")
        if hit:
            body = re.sub(r"\[第\s*\d+\s*页\]", "", hit.group(1))
            body = re.sub(r"(?m)^-\d{1,4}-$", "", body)
            body = re.sub(r"\s+", "", body).strip("；;，,。")
            if len(body) >= 8:
                data["bidContent"] = body[:400]


async def parse_invitation(
    db: AsyncSession,
    upload: UploadFile,
    *,
    kb_ids: list[str] | None = None,
    current: BidBrief | None = None,
    quote_upload: UploadFile | None = None,
    quote_record_ids: list[str] | None = None,
    created_by: int | None = None,
    persist_library: bool = True,
    volume_locked: bool = False,
) -> dict[str, Any]:
    from api.services.tenders.performance_tracker import PerformanceTracker
    tracker = PerformanceTracker()
    tracker.start_total()
    path = await _save_invitation(upload)
    quote_path = None
    if quote_upload is not None and (quote_upload.filename or "").strip():
        quote_path = await _save_upload(quote_upload)
    extracted = await extract_text_from_file(path, ext=path.suffix.lower().lstrip("."))
    invitation = (extracted.text or "").strip()
    invitation_id = save_invitation_text(invitation, stem=path.stem)
    settings = get_settings()
    max_ocr = max(0, int(settings.ocr_max_pages))
    if len(invitation) < 20:
        hint = "邀请书解析正文过短，请换可检索的 Word（.doc / .docx）"
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

    kb_hits, kb_notes = await _recall_knowledge(db, invitation, kb_ids or [], bidder_name=(current.bidderName if current else ""))
    if contains_foreign_bidder(invitation, (current.bidderName if current else "")):
        kb_notes = [
            "正文出现其他公司名称，请确认上传的是甲方邀请书/招标文件，而不是他人已填的投标书",
            *kb_notes,
        ]
    playbooks = await load_history_playbooks(db, bidder_name=(current.bidderName if current else ""))
    from api.services.tenders.library_kb import (
        catalog_placeholders,
        list_library_items,
        performance_from_library,
        persist_parsed_materials,
    )

    catalog = await catalog_placeholders(db)
    catalog_rows = await list_library_items(db)
    # ========== 性能追踪开始 ==========
    tracker.set_context(len(invitation), upload.filename or "")

    client = await build_llm_client(db, "fast")

    from api.services.tenders.extract_chunks_optimized import extract_chunks, split_invitation
    system_prompt = get_prompt("extract_system_optimized")
    system_prompt += "\nReturn exact source material titles; catalog key mapping is performed locally against the complete catalog. Never omit unknown materials."
    system_prompt += '\nAlso return materialReviewComplete (boolean) and materialIntents (array). Review ALL text for mandatory, conditional or implied submission requirements. Each materialIntents item must have title, evidence (exact verbatim source clause), section (source parent heading), optional key, condition and mode (attachment/form/statement). Include unfamiliar materials. Set materialReviewComplete=true only after reviewing this entire block. These fields are required even when materialIntents is empty.'
    system_prompt += "\nBidder: " + (current.bidderName if current else "")
    with tracker.track("llm_extract"):
        patch = await extract_chunks(invitation, client, system_prompt, lambda text: text,
                                     parse_extract_payload, enable_json_mode=True)
        tracker.metrics.llm_chunk_count = len(split_invitation(invitation))
    # ========== 一次性预解析文档结构 ==========
    from api.services.tenders.document_parser import parse_document_structure
    with tracker.track("document_parse"):
        doc_structure = parse_document_structure(invitation)

    brief, filled = apply_extract_patch(current or default_brief(), patch)
    brief = apply_invite_letter_fields(brief, invitation, file_name=upload.filename or "")
    brief.invitationId = invitation_id

    # ========== 并行化独立DB查询 ==========
    with tracker.track("db_query"):
        # 批量执行独立查询
        import asyncio
        # AsyncSession is mutable transaction state and cannot be shared by
        # concurrently executing queries.
        company_profile = await latest_company_profile(db, bidder_name=brief.bidderName)
        catalog = await catalog_placeholders(db)
        catalog_rows = await list_library_items(db)

    brief, company_filled = fill_empty_company_fields(brief, current, company_profile)
    for key in company_filled:
        if key not in filled:
            filled.append(key)
    extras = slots_from_payload(patch, catalog=catalog)
    before_keys = {item.key for item in catalog if item.key}

    if persist_library:
        with tracker.track("db_query"):
            extras = await persist_parsed_materials(db, extras, created_by=created_by)
            # Re-fetch catalog after persist (may have new items)
            catalog = await catalog_placeholders(db)

    created_keys = [item.key for item in extras if item.key and item.key not in before_keys]
    has_agent = bool(brief.authEnabled)

    # First materials pass
    with tracker.track("materials_match"):
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
    from api.services.tenders.classifier import (
        apply_bid_requirements,
        bind_requirements,
        biz_clause_filled,
        extract_requirements,
        extract_veto_requirements,
        merge_veto_requirements,
    )
    from api.services.tenders.outline import (
        choose_layout_mode,
        extract_auth_need,
        extract_outline,
        has_invite_format_chapter,
        hydrate_tech_excerpts,
        unmash_invitation_text,
    )
    from api.services.tenders.performance import (
        extract_performance_requirement,
        format_requirement,
        merge_performance_requirement,
        pad_performance_placeholders,
        performance_match_issues,
        requirement_from_payload,
    )

    # ========== 本地规则处理（使用预解析结构） ==========
    with tracker.track("outline_extract"):
        chapter, outline_items = extract_outline(invitation)
        if path.suffix.lower() == '.docx':
            from api.services.tenders.native_format import word_format_section

            native_chapter, native_section = word_format_section(path)
            if native_chapter:
                chapter = native_chapter
                _, native_items = extract_outline(native_section)
                if native_items:
                    outline_items = native_items
        brief.authNeed = extract_auth_need(invitation, outline_items)

    brief.authEnabled = bool(brief.authEnabled)
    brief.layoutMode = choose_layout_mode(chapter, outline_items)
    brief.requirementsDriven = False
    brief.formatConfirmed = False
    brief.outlineChapter = chapter
    brief.outlineItems = hydrate_tech_excerpts(outline_items)

    with tracker.track("format_extract"):
        brief.documentFormat = extract_document_format(invitation)
        # Use cached tender_no from structure
        from api.services.tenders.document_parser import extract_tender_no_from_structure
        tender_no = extract_tender_no_from_structure(doc_structure)
        if not tender_no:
            tender_no = extract_tender_no(invitation)  # Fallback
    if tender_no and not _missing_project_fact(tender_no) and _missing_project_fact(brief.tenderNo):
        brief.tenderNo = tender_no
    for key in ("projectName", "tenderer", "tenderNo", "bidContent"):
        if str(getattr(brief, key, "") or "").strip() and key not in filled:
            filled.append(key)

    # Performance requirement extraction
    with tracker.track("performance_extract"):
        brief.performanceRequirement = merge_performance_requirement(
            extract_performance_requirement(invitation),
            requirement_from_payload(patch),
            invitation=invitation,
        )

    # Requirements extraction - use unmashed text from structure
    with tracker.track("requirements_extract"):
        from api.services.tenders.document_parser import unmash_text_from_structure
        unmashed_text = unmash_text_from_structure(doc_structure)
        req_notes = apply_bid_requirements(
            brief,
            merge_veto_requirements(
                extract_requirements(unmashed_text),
                extract_veto_requirements(invitation),
            ),
        )
    brief.extraPlaceholders = extras
    brief.requiredSlotKeys = [slot.key for slot in extras if slot.key]
    brief.includeSlotKeys = list(brief.requiredSlotKeys)
    brief.includePlaceholders = False

    with tracker.track("db_query"):
        quote_record_sheet = await _quote_record_sheet(db, created_by, quote_record_ids)

    quote_note, brief, quote_filled = _merge_quote(
        brief,
        patch,
        invitation,
        path,
        quote_path,
        record_sheet=quote_record_sheet,
        source_name=quote_upload.filename if quote_upload is not None else '',
    )
    if quote_filled:
        filled = [*filled, "quoteLines"]
        if "bidPriceYuan" not in filled and brief.bidPriceYuan > 0:
            filled.append("bidPriceYuan")
    from api.services.tenders.tables import apply_format_table_headers

    header_notes = apply_format_table_headers(brief)
    kb_fmt_notes: list[str] = []
    if (kb_ids or []) and not has_invite_format_chapter(chapter):
        with tracker.track("kb_recall"):
            fmt_hits, fmt_err = await _recall_knowledge(
                db,
                invitation,
                kb_ids or [],
                query=_KB_FORMAT_QUERY,
                quiet=True,
                bidder_name=brief.bidderName,
            )
        kb_fmt_notes.extend(fmt_err)
        kb_fmt_notes.extend(apply_kb_format_suggestions(brief, chapter, fmt_hits or kb_hits))
        outline_items = list(brief.outlineItems or [])

    # Parallel DB queries for performance library
    with tracker.track("db_query"):
        lib_perf = await performance_from_library(db, extract_missing=False, bidder_name=brief.bidderName) if brief.bidderName else []
    if lib_perf or brief.performanceLines:
        brief.performanceLines = merge_performance_lines(
            brief.performanceLines,
            lib_perf,
            requirement=brief.performanceRequirement,
        )
    brief.performanceLines = pad_performance_placeholders(
        brief.performanceLines,
        brief.performanceRequirement,
    )
    if brief.performanceLines and "performanceLines" not in filled:
        filled = [*filled, "performanceLines"]

    # Materials matching - track separately
    with tracker.track("materials_match"):
        # Outline/requirements are now available; an earlier LLM-only material list
        # must not keep the matching library keys out of this bid.
        required_keys, include_keys = this_bid_keys(
            extra=extras, catalog=catalog, required_keys=required_keys, include_keys=include_keys,
            has_agent=has_agent, outline_items=brief.outlineItems,
            requirements=brief.bidRequirements, source_text=invitation,
            material_demands=brief.materialDemands,
        )
        brief.requiredSlotKeys, brief.includeSlotKeys = required_keys, include_keys
        slots = collect_slots(extras, catalog=catalog, include_keys=include_keys,
                              outline_items=brief.outlineItems)

    # catalog_rows already loaded at start, reuse it
    file_counts = {
        str(row.get("key") or ""): int(row.get("fileCount") or 0)
        for row in catalog_rows
        if row.get("key")
    }
    brief.bidRequirements = bind_requirements(
        brief.bidRequirements,
        catalog=catalog,
        file_counts=file_counts,
        outline_items=brief.outlineItems,
        performance_lines=brief.performanceLines,
        deviation_lines=brief.deviationLines,
        layout_mode=brief.layoutMode,
        biz_filled=biz_clause_filled(brief),
    )
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
        *kb_fmt_notes,
        *req_notes,
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
    fmt_note = ""
    if brief.layoutMode == "outline" and path.suffix.lower() == ".docx":
        from api.services.tenders.pdf_format import prepare_word_format_docx

        fmt_note = await asyncio.to_thread(prepare_word_format_docx, path, invitation_id)
    elif brief.layoutMode == "outline" and path.suffix.lower() == ".pdf":
        fmt_note = "格式章只从 Word 原件截取，请将招标文件另存为 .docx 后重新上传"
    if fmt_note:
        notes.append(fmt_note)
        src = tender_invitations_dir() / f"{invitation_id}.format.docx"
        if src.is_file():
            import shutil

            from api.services.tenders.assets import tenders_output_dir

            dest = tenders_output_dir() / src.name
            await asyncio.to_thread(shutil.copyfile, src, dest)
            notes.append(f"格式章空白稿已保存，可预览：{dest.name}")
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
    from api.services.tenders.requirement_bid import prepare_requirements_bid

    requirement_notes = prepare_requirements_bid(brief, text=invitation)
    from api.services.tenders.material_intents import recognize_material_demands

    with tracker.track("material_review"):
        notes.extend(await recognize_material_demands(
            brief,catalog=catalog,text=invitation,source_path=path,client=client,db=db,persist_source=True,semantic_payload=patch))
    extras = brief.extraPlaceholders
    required_keys = list(brief.requiredSlotKeys)
    include_keys = list(brief.includeSlotKeys)
    existing_catalog_keys = {str(row.get('key') or '') for row in catalog_rows}
    catalog_rows.extend({'key':slot.key,'title':slot.title,'hint':slot.hint,'fileCount':0}
        for slot in extras if slot.key not in existing_catalog_keys)
    # Requirements synthesis (or recovery of auto-numbered native headings) can
    # add sections after the first matching pass. Return keys for the final outline.
    required_keys, include_keys = this_bid_keys(
        extra=extras, catalog=catalog, required_keys=required_keys, include_keys=include_keys,
        has_agent=has_agent, outline_items=brief.outlineItems,
        requirements=brief.bidRequirements, source_text=invitation,
        material_demands=brief.materialDemands,
    )
    brief.requiredSlotKeys, brief.includeSlotKeys = required_keys, include_keys
    slots = collect_slots(extras, catalog=catalog, include_keys=include_keys,
                          outline_items=brief.outlineItems)
    previous_match_notes = set(attachment_match_notes(attachment_match))
    attachment_match = build_attachment_match(
        catalog_rows, required_keys=required_keys, include_keys=include_keys,
        created_keys=created_keys,
    )
    notes = [note for note in notes if note not in previous_match_notes]
    notes.extend(attachment_match_notes(attachment_match))
    if brief.requirementsDriven:
        notes = [note for note in notes if '公司固定模板' not in note
                 and '承诺函只填单位和日期' not in note]
        notes.extend(requirement_notes)
        brief.bidRequirements = bind_requirements(
            brief.bidRequirements, catalog=catalog, file_counts=file_counts,
            outline_items=brief.outlineItems, performance_lines=brief.performanceLines,
            deviation_lines=brief.deviationLines, layout_mode=brief.layoutMode,
            biz_filled=biz_clause_filled(brief),
        )
    if brief.authNeed == "required":
        notes.append("招标书写了须授权委托；默认法人自签，要用委托人请打开「启用授权委托」")
    elif brief.authNeed == "optional":
        notes.append("招标书有授权委托书格式；未打开「启用授权委托」则跳过该页")
    from api.services.tenders.library_kb import legal_persons_from_library

    with tracker.track("db_query"):
        persons = await legal_persons_from_library(db, extract_missing=True)
    if len(persons) > 1:
        names = "、".join((item.get("name") or item.get("idNo") or "") for item in persons[:6])
        notes.append(
            f"资料库有 {len(persons)} 位法定代表人（{names}），请在下一步下拉选择本标使用的人和身份证"
        )
    elif len(persons) == 1:
        who = persons[0].get("name") or persons[0].get("idNo") or ""
        notes.append(f"资料库已识别法定代表人 {who}，下一步可核对；生成时只贴这一人的身份证正反面")
    notes.extend(format_brief_notes(brief.documentFormat))
    from api.services.tenders.classifier import apply_volume_binding

    brief = apply_volume_binding(brief, invitation, keep_volume=volume_locked)
    if (brief.volumeNote or "").strip():
        notes.append(brief.volumeNote)

    preview = invitation[:1200] + ("…" if len(invitation) > 1200 else "")

    with tracker.track("db_query"):
        agent_hint = await latest_agent_profile(db, bidder_name=brief.bidderName)

    # ========== 性能追踪结束 ==========
    tracker.finish_total()
    tracker.log_summary()

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
        "agentHint": agent_hint,
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
        "controlPriceYuan": "招标控制价",
        "safetyFeeYuan": "安全生产措施费",
        "provisionalYuan": "暂列金额",
        "primeCostYuan": "专业工程暂估价",
        "bidBondYuan": "投标保证金",
        "pmName": "项目经理",
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
        "techPlanNote": "技术方案说明",
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
    *, bidder_name: str = "",
) -> str:
    body = invitation
    parts = [
        f"投标人（以本次 brief 的 bidderName 为准，不得从招标范例替换）：{bidder_name or '【待补充】'}",
        "请阅读邀请书正文并抽出 JSON。",
        "",
        "【邀请书正文】",
        body,
    ]
    if catalog_rows:
        from api.services.tenders.library_kb import catalog_prompt_lines

        parts.append("")
        parts.append("【投标资料库清单·能等价映射则复用 key；未知资料保留名称和原文，不得遗漏】")
        parts.append(catalog_prompt_lines([{**row, "hint": ""} for row in catalog_rows]))
    history_block = playbooks_prompt_block((playbooks or [])[:2])
    if history_block:
        parts.append("")
        parts.append(history_block)
    safe_hits = [
        hit
        for hit in (kb_hits or [])
        if not should_skip_kb_retrieval(
            name=str(hit.get("name") or ""),
            bidder_name=bidder_name,
            content=str(hit.get("content") or ""),
            tags=hit.get("tags") if isinstance(hit.get("tags"), list) else None,
        )
    ]
    if safe_hits:
        parts.append("")
        parts.append("【知识库摘录·仅对照资格条款，禁止套用历史投标书或他司合同】")
        for hit in safe_hits[:2]:
            name = str(hit.get("name") or "资料")
            content = str(hit.get("content") or "").strip()[:1800]
            if content:
                parts.append(f"—— {name} ——\n{content}")
    return "\n".join(parts)


def apply_kb_format_suggestions(
    brief: BidBrief,
    chapter: str,
    clips: list[dict[str, Any]] | None,
) -> list[str]:
    """本标没有格式章时，用知识库摘录补目录标题和表头，不抄函/报价正文。"""
    from api.services.tenders.format_rules import extract_document_format
    from api.services.tenders.outline import (
        choose_layout_mode,
        has_invite_format_chapter,
        hydrate_tech_excerpts,
        titles_from_format_text,
    )
    from api.services.tenders.tables import extract_dev_headers, extract_quote_headers

    if has_invite_format_chapter(chapter):
        brief.layoutMode = 'outline'
        brief.requirementsDriven = False
        return []
    parts: list[str] = []
    names: list[str] = []
    for hit in clips or []:
        text = str(hit.get("content") or "").strip()
        if not text:
            continue
        if should_skip_kb_retrieval(
            name=str(hit.get("name") or ""),
            bidder_name=brief.bidderName,
            content=text,
            tags=hit.get("tags") if isinstance(hit.get("tags"), list) else None,
        ):
            continue
        parts.append(text[:4000])
        label = str(hit.get("name") or "").strip()
        if label and label not in names:
            names.append(label)
    blob = "\n\n".join(parts)
    if not blob.strip():
        return []
    src = "、".join(names[:3]) if names else "知识库"
    notes: list[str] = []
    active = [item for item in (brief.outlineItems or []) if not item.skipped]
    if not active:
        items = titles_from_format_text(blob)
        if items:
            brief.outlineItems = hydrate_tech_excerpts(items)
            brief.layoutMode = choose_layout_mode(chapter, brief.outlineItems)
            notes.append(f"目录 {len(items)} 条来自知识库（{src}），请核对，不套用其中正文")
    if not any(str(h or "").strip() for h in (brief.quoteHeaders or [])):
        layout = extract_quote_headers(blob)
        if layout is not None:
            brief.quoteHeaders = list(layout.titles)
            brief.quoteRoles = list(layout.roles)
            notes.append("报价表表头来自知识库，请核对")
    if not any(str(h or "").strip() for h in (brief.techDevHeaders or [])):
        layout = extract_dev_headers(blob)
        if layout is not None:
            brief.techDevHeaders = list(layout.titles)
            notes.append("技术偏离表表头来自知识库，请核对")
    if not any(str(h or "").strip() for h in (brief.bizDevHeaders or [])):
        layout = extract_dev_headers(blob)
        if layout is not None:
            brief.bizDevHeaders = list(layout.titles)
            notes.append("商务偏离表表头来自知识库，请核对")
    fmt = brief.documentFormat
    if fmt is None or not fmt.specified:
        sug = extract_document_format(blob)
        if sug.specified:
            brief.documentFormat = sug
            notes.append("排版建议来自知识库，请核对")
    return notes


async def _recall_knowledge(
    db: AsyncSession,
    invitation: str,
    kb_ids: list[str],
    *,
    query: str | None = None,
    quiet: bool = False,
    bidder_name: str = "",
) -> tuple[list[dict[str, Any]], list[str]]:
    notes: list[str] = []
    ids = [x.strip() for x in kb_ids if str(x).strip()]
    if not ids:
        return [], notes
    q = (query or "").strip() or (" ".join(invitation.split())[:240] + _KB_QUAL_TAIL)
    try:
        docs = await search_knowledge_docs(db, query=q, top_k=3, kb_ids=ids, max_drawings=0)
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
            bidder_name=bidder_name,
            content=content,
            tags=doc.get("tags") if isinstance(doc.get("tags"), list) else None,
        ):
            skipped += 1
            continue
        clip = content[:2000]
        used += len(clip)
        if used > _KB_MAX_CHARS:
            break
        kept.append({"name": doc.get("name"), "docId": doc.get("doc_id"), "content": clip, "tags": doc.get("tags") or []})
    if skipped:
        notes.append(f"已忽略 {skipped} 篇疑似投标书或其他公司材料的知识库文档")
    if quiet:
        return kept, notes
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
    record_sheet=None,
    source_name: str = '',
) -> tuple[list[str], BidBrief, bool]:
    del patch, invitation, invite_path
    notes: list[str] = []
    from api.services.tenders.quote import quote_source_notes
    sheet = parse_quote_path(quote_path) if quote_path is not None else None
    source = "file" if sheet is not None else ""
    if sheet is None and record_sheet is not None:
        sheet = record_sheet
        source = "record"
    if sheet is None and quote_path is not None:
        notes.append("报价清单未能按表格解析，生成时不填写报价分项。")
    if sheet is None:
        if quote_path is None:
            notes.append("未上传报价清单，生成时不填写报价分项。未采用模型或历史投标书中的台数。")
        if (brief.quoteAutoPrice
                and float(brief.bidPriceYuan or 0) == float(brief.quoteSourceIncTax)):
            brief.bidPriceYuan = 0
            notes.append('已清空上次清单自动填入的总价，避免沿用其他项目报价')
        if brief.quoteLines:
            data = brief.model_dump()
            data["quoteLines"] = []
            data["quoteSource"] = ""
            data["quoteSourceIncTax"] = 0
            brief = BidBrief.model_validate(data)
            notes.append("已清空上次表单里的分项，避免沿用其他项目工程量")
        brief.quoteTitle = ''
        brief.quoteSource = ''
        brief.quoteSourceIncTax = 0
        brief.quoteHeaders = []
        brief.quoteRoles = []
        brief.quoteSourceName = source_name or ''
        brief.quoteSourceTitle = ''
        brief.quoteSourceProjects = []
        brief.quoteMatchStatus = ''
        brief.quoteMatchNote = ''
        brief.quoteAutoPrice = False
        return notes, brief, False
    before = float(brief.bidPriceYuan or 0)
    brief = attach_sheet(brief, sheet, source=source,
                         source_name=source_name or (Path(quote_path).name if quote_path else '报价记录'))
    notes.extend(quote_source_notes(brief))
    if brief.quoteMatchStatus == 'unmatched':
        return notes, brief, False
    if source == "record":
        notes.append(f"已引用报价记录分项 {len(sheet.lines)} 项（《{sheet.title}》，清单含税 {sheet.total_inc_tax}）")
    else:
        notes.append(f"已解析分项 {len(sheet.lines)} 项（《{sheet.title}》，清单含税 {sheet.total_inc_tax}）")
    if before <= 0 and brief.bidPriceYuan > 0:
        notes.append("投标总价已按清单含税合计填入，可按二次报价再改")
    return notes, brief, True


_INVITE_WORD = {".doc", ".docx"}


def invitation_word_ext(filename: str | None) -> str:
    name = (filename or "").replace("\\", "/").split("/")[-1].strip()
    ext = Path(name).suffix.lower()
    if ext not in _INVITE_WORD:
        raise AppError(ErrorCode.VALIDATION, "投标邀请书只支持 .doc 和 .docx", status_code=422)
    return ext


async def _dump_upload(upload: UploadFile, dest: Path) -> None:
    settings = get_settings()
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


async def _save_upload(upload: UploadFile):
    filename = (upload.filename or "invitation.bin").replace("\\", "/").split("/")[-1]
    ext = assert_supported(sniff_extension(filename, upload.content_type))
    dest = tender_invitations_dir() / f"{uuid4().hex[:12]}.{ext}"
    await _dump_upload(upload, dest)
    return dest


def _doc_to_docx(src: Path, dest: Path) -> None:
    convert_doc_to_docx(src, dest)


async def _save_invitation(upload: UploadFile):
    ext = invitation_word_ext(upload.filename)
    if ext == ".docx":
        return await _save_upload(upload)
    raw = tender_invitations_dir() / f"{uuid4().hex[:12]}.doc"
    await _dump_upload(upload, raw)
    docx = raw.with_suffix(".docx")
    try:
        await asyncio.to_thread(_doc_to_docx, raw, docx)
    except Exception as exc:
        logger.warning("doc to docx failed file=%r: %s", upload.filename, exc)
        raw.unlink(missing_ok=True)
        docx.unlink(missing_ok=True)
        raise AppError(
            ErrorCode.VALIDATION,
            "旧版 .doc 打不开，请在 Word 里另存为 .docx 后再上传",
            status_code=422,
        ) from exc
    raw.unlink(missing_ok=True)
    return docx


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


async def _quote_record_sheet(db: AsyncSession, user_id: int | None, public_ids: list[str] | None):
    ids = [str(x).strip() for x in (public_ids or []) if str(x).strip()]
    if not ids:
        return None
    from api.services.quotes.records import load_owned

    rows = await load_owned(db, user_id=int(user_id or 0), public_ids=ids)
    sheet = sheet_from_quote_records(rows)
    if sheet is None:
        raise AppError(ErrorCode.VALIDATION, "所选报价记录没有可用明细", status_code=422)
    return sheet
