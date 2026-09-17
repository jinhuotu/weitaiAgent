"""生成后对照邀请书做 AI 质检：符合度 + 缺失清单。"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from api.services.layouts.parse import extract_json_object
from api.services.tenders.assets import load_invitation_text, tenders_output_dir
from api.services.tenders.categories import HIGH_DISQUALIFY_KEYS, tech_plan_text
from api.services.tenders.schema import BidBrief, OutlineItem
from common.errors import AppError, ErrorCode
from common.times import to_epoch_ms

logger = logging.getLogger("api.tenders.qa")

_RECORD_ID_RE = re.compile(r"^[a-f0-9]{8,32}$", re.I)
_COMPACT = re.compile(r"[\s/（）()【】\[\]:：·,，。、\-—_“”\"']+")
_BID_TEXT_MAX = 80_000
_LLM_INVITE_CHARS = 12_000
_LLM_BID_CHARS = 12_000
_MAX_QA_UPLOAD = 40 * 1024 * 1024
_SOURCE_GENERATED = "generated"
_SOURCE_UPLOAD = "upload"
QA_VOLUMES = ("business", "technical")

_QA_JSON_KEYS = ("similarityScore", "missing", "summary")

_SYSTEM = """你是投标文件质检员。对照甲方邀请书/招标文件，检查已生成的投标响应文件是否覆盖必要内容。
注意：两份文件本来就不是同一篇，不要按全文字面重合率打分。
打分含义是「邀请书要求被响应的完整程度」0-100。
硬性规则：
1. 只根据给出的邀请书正文和投标文件正文判断，禁止编造未出现的条款。
2. 投标人是河南伟泰光电科技有限公司。邀请书范例里的其他公司名称不算缺失。
3. 扫描件标题已写入但标注待补/虚线框，算「材料未附」，不要当成整章缺失。
4. 用户消息会标明当前是商务标还是技术标：只评这一卷该有的内容，不要把另一卷的章节算作本卷缺失。
5. 只输出一个 JSON 对象，不要思考过程、不要 Markdown。
JSON 字段：
similarityScore（整数 0-100，响应完整度）,
summary（一两句中文总评）,
missing（对象数组，每项 title/reason/severity/category；
  severity 只能是 disqualify=废标风险、deduct=扣分风险、suggest=建议补全；
  category 只能是 outline/qualification/commercial/technical/quote/other）。
没有缺项时 missing 为 []。"""

SEVERITY_DISQUALIFY = "disqualify"
SEVERITY_DEDUCT = "deduct"
SEVERITY_SUGGEST = "suggest"
_SEV_RANK = {SEVERITY_DISQUALIFY: 0, SEVERITY_DEDUCT: 1, SEVERITY_SUGGEST: 2}

_OUTLINE_DISQUALIFY_KINDS = frozenset({"letter", "legal_id", "auth", "quote"})


def qa_report_path(public_id: str) -> Path:
    pid = (public_id or "").strip()
    if not _RECORD_ID_RE.fullmatch(pid):
        raise AppError(ErrorCode.BAD_REQUEST, "invalid tender record id", status_code=400)
    return tenders_output_dir() / f"{pid}.qa.json"


def normalize_qa_volume(raw: str | None) -> str:
    v = (raw or "").strip().lower()
    return v if v in QA_VOLUMES else "business"


def _is_report(item: Any) -> bool:
    return isinstance(item, dict) and item.get("similarityScore") is not None


def _read_qa_file(public_id: str) -> dict[str, Any] | None:
    try:
        path = qa_report_path(public_id)
    except AppError:
        return None
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def load_qa_bundle(public_id: str) -> dict[str, dict[str, Any]]:
    data = _read_qa_file(public_id)
    if not data:
        return {}
    nested = {k: dict(data[k]) for k in QA_VOLUMES if _is_report(data.get(k))}
    if nested:
        return nested
    if _is_report(data):
        return {normalize_qa_volume(str(data.get("volume") or "")): data}
    return {}


def unlink_qa_report(public_id: str, volume: str | None = None) -> None:
    pid = (public_id or "").strip()
    if not _RECORD_ID_RE.fullmatch(pid):
        return
    path = tenders_output_dir() / f"{pid}.qa.json"
    if not volume:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        unlink_qa_upload(pid)
        return
    bundle = load_qa_bundle(pid)
    bundle.pop(normalize_qa_volume(volume), None)
    if not bundle:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        return
    path.write_text(json.dumps(bundle, ensure_ascii=False), encoding="utf-8")


def qa_upload_path(public_id: str) -> Path:
    pid = (public_id or "").strip()
    if not _RECORD_ID_RE.fullmatch(pid):
        raise AppError(ErrorCode.BAD_REQUEST, "invalid tender record id", status_code=400)
    return tenders_output_dir() / f"{pid}.qa-upload.docx"


def unlink_qa_upload(public_id: str) -> None:
    pid = (public_id or "").strip()
    if not _RECORD_ID_RE.fullmatch(pid):
        return
    path = tenders_output_dir() / f"{pid}.qa-upload.docx"
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def save_qa_upload(public_id: str, data: bytes) -> Path:
    blob = data or b""
    if len(blob) < 80:
        raise AppError(ErrorCode.VALIDATION, "文件太小，请上传有效的 Word（.docx）", status_code=422)
    if len(blob) > _MAX_QA_UPLOAD:
        raise AppError(ErrorCode.VALIDATION, "Word 超过 40MB，请压缩后再传", status_code=422)
    if blob[:2] != b"PK":
        raise AppError(ErrorCode.VALIDATION, "请上传 Word（.docx）", status_code=422)
    path = qa_upload_path(public_id)
    path.write_bytes(blob)
    return path


def qa_summary(public_id: str) -> dict[str, Any]:
    report = load_qa_report(public_id)
    if not report:
        return {"qaScore": None, "qaGrade": None, "qaSource": None, "qaCheckedAt": None}
    return {
        "qaScore": report.get("similarityScore"),
        "qaGrade": report.get("grade"),
        "qaSource": report.get("source") or _SOURCE_GENERATED,
        "qaCheckedAt": report.get("checkedAt"),
        "qaVolume": report.get("volume") or "business",
    }


def load_qa_report(public_id: str, volume: str | None = None) -> dict[str, Any] | None:
    bundle = load_qa_bundle(public_id)
    if not bundle:
        return None
    if volume:
        hit = bundle.get(normalize_qa_volume(volume))
        return dict(hit) if hit else None
    latest = max(bundle.values(), key=lambda r: int(r.get("checkedAt") or 0))
    return dict(latest)


def save_qa_report(
    public_id: str,
    report: dict[str, Any],
    volume: str | None = None,
) -> None:
    vol = normalize_qa_volume(volume or str(report.get("volume") or ""))
    row = dict(report)
    row["volume"] = vol
    bundle = load_qa_bundle(public_id)
    bundle[vol] = row
    path = qa_report_path(public_id)
    path.write_text(json.dumps(bundle, ensure_ascii=False), encoding="utf-8")


def compact_text(text: str) -> str:
    return _COMPACT.sub("", text or "").lower()


def clip_text(text: str, limit: int) -> str:
    raw = text or ""
    if len(raw) <= limit:
        return raw
    return raw[:limit] + "\n…[已截断]"


def title_in_text(haystack: str, title: str, *, min_len: int = 4) -> bool:
    needle = compact_text(title)
    blob = compact_text(haystack)
    if len(needle) < min_len:
        return True
    if needle in blob:
        return True
    if len(needle) >= 10 and needle[:8] in blob:
        return True
    return False


def _gap(
    title: str,
    reason: str,
    *,
    severity: str,
    category: str,
    source: str = "rule",
) -> dict[str, str]:
    sev = severity if severity in _SEV_RANK else SEVERITY_SUGGEST
    return {
        "title": (title or "").strip()[:80] or "未命名项",
        "reason": (reason or "").strip()[:240],
        "severity": sev,
        "category": (category or "other").strip()[:24] or "other",
        "source": source,
    }


def _score_bucket(found: int, total: int) -> dict[str, int]:
    if total <= 0:
        return {"found": 0, "total": 0, "score": 100}
    return {"found": found, "total": total, "score": round(100 * found / total)}


def _outline_severity(item: OutlineItem) -> str:
    if item.kind in _OUTLINE_DISQUALIFY_KINDS:
        return SEVERITY_DISQUALIFY
    if item.kind in {"scan", "performance", "commitment_copy"}:
        return SEVERITY_DEDUCT
    return SEVERITY_SUGGEST


def rule_inspect(
    brief: BidBrief,
    bid_text: str,
    *,
    missing_slot_titles: list[str] | None = None,
    volume: str | None = None,
) -> dict[str, Any]:
    """规则核对：组卷目录、关键商务字段、报价行、资格扫描件。"""
    gaps: list[dict[str, str]] = []
    blob = compact_text(bid_text)
    vol = (volume or "").strip().lower()
    if vol not in QA_VOLUMES:
        vol = ""
    if vol:
        from api.services.tenders.outline import items_for_volume

        outline_items = items_for_volume(brief, vol)
    else:
        outline_items = brief.outlineItems or []
    outline_found = outline_total = 0
    for item in outline_items:
        if item.skipped or not item.required:
            continue
        title = (item.title or "").strip()
        if not title:
            continue
        outline_total += 1
        if title_in_text(blob, title):
            outline_found += 1
            continue
        gaps.append(
            _gap(
                title,
                "邀请书组卷目录要求此项，生成稿中未找到对应标题",
                severity=_outline_severity(item),
                category="outline",
            )
        )

    commercial_found = commercial_total = 0
    field_checks: list[tuple[str, str, str, str]] = []
    if (brief.projectName or "").strip():
        field_checks.append(
            (brief.projectName, "项目名称未写入投标文件", SEVERITY_DISQUALIFY, "commercial")
        )
    if (brief.tenderer or "").strip():
        field_checks.append(
            (brief.tenderer, "招标人名称未出现在投标文件", SEVERITY_DEDUCT, "commercial")
        )
    if vol != "technical" and float(brief.bidPriceYuan or 0) > 0:
        price = (
            str(int(brief.bidPriceYuan))
            if float(brief.bidPriceYuan).is_integer()
            else str(brief.bidPriceYuan)
        )
        field_checks.append((price, "投标总价未出现在投标文件", SEVERITY_DISQUALIFY, "commercial"))
    if vol != "technical" and int(brief.deliveryDays or 0) > 0:
        field_checks.append(
            (
                str(int(brief.deliveryDays)),
                "供货期天数未出现在投标文件",
                SEVERITY_DEDUCT,
                "commercial",
            )
        )
    for needle, reason, severity, category in field_checks:
        commercial_total += 1
        if title_in_text(blob, needle, min_len=2):
            commercial_found += 1
        else:
            gaps.append(_gap(needle[:40], reason, severity=severity, category=category))

    quote_found = quote_total = 0
    if vol != "technical":
        names = [
            (row.name or "").strip() for row in (brief.quoteLines or []) if (row.name or "").strip()
        ]
        for name in names[:24]:
            quote_total += 1
            if title_in_text(blob, name, min_len=2):
                quote_found += 1
            else:
                gaps.append(
                    _gap(
                        name,
                        "邀请书/清单中的分项未出现在投标报价表",
                        severity=SEVERITY_DISQUALIFY,
                        category="quote",
                    )
                )

    tech_found = tech_total = 0
    if vol != "business":
        has_tech_outline = any(
            item.kind == "tech_plan" and not item.skipped for item in outline_items
        )
        if has_tech_outline or tech_plan_text(brief):
            tech_total += 1
            if tech_plan_text(brief) and (
                title_in_text(blob, "实施方案")
                or title_in_text(blob, "技术标")
                or title_in_text(blob, tech_plan_text(brief)[:12], min_len=4)
            ):
                tech_found += 1
            elif title_in_text(blob, "实施方案") or title_in_text(blob, "技术标"):
                tech_found += 1
            else:
                gaps.append(
                    _gap(
                        "技术标实施方案",
                        "邀请书要求技术方案/实施方案，生成稿中未见对应章节",
                        severity=SEVERITY_DEDUCT,
                        category="technical",
                    )
                )
        if not any((row.requirement or "").strip() for row in (brief.deviationLines or [])):
            if any(item.kind == "tech_dev" and not item.skipped for item in outline_items):
                tech_total += 1
                gaps.append(
                    _gap(
                        "技术偏离表",
                        "尚未填写技术偏差，未响应招标技术要求会大量扣分",
                        severity=SEVERITY_DEDUCT,
                        category="technical",
                    )
                )
        else:
            tech_total += 1
            tech_found += 1

    from api.services.tenders.categories import slot_volume

    mat_found = mat_total = 0
    missing_set = {t for t in (missing_slot_titles or []) if t}
    slot_map = {
        item.key: (item.title or item.key) for item in (brief.extraPlaceholders or []) if item.key
    }
    seen_titles: set[str] = set()
    for key in brief.requiredSlotKeys or []:
        title = slot_map.get(key) or key
        if not title or title in seen_titles:
            continue
        if vol and slot_volume(key, title) != vol:
            continue
        seen_titles.add(title)
        mat_total += 1
        if title in missing_set:
            gaps.append(
                _gap(
                    title,
                    "邀请书要求的资格/证明材料未上传，Word 中多为虚线框占位",
                    severity=(
                        SEVERITY_DISQUALIFY if key in HIGH_DISQUALIFY_KEYS else SEVERITY_DEDUCT
                    ),
                    category="qualification",
                )
            )
            continue
        if title_in_text(blob, title, min_len=4):
            mat_found += 1
            continue
        gaps.append(
            _gap(
                title,
                "邀请书要求的资料在投标文件中未找到对应标题",
                severity=(
                    SEVERITY_DISQUALIFY if key in HIGH_DISQUALIFY_KEYS else SEVERITY_DEDUCT
                ),
                category="qualification",
            )
        )
    for title in missing_set:
        if title in seen_titles:
            continue
        seen_titles.add(title)
        mat_total += 1
        gaps.append(
            _gap(
                title,
                "邀请书要求的资格/证明材料未上传，Word 中多为虚线框占位",
                severity=SEVERITY_DEDUCT,
                category="qualification",
            )
        )

    fmt = brief.documentFormat
    if fmt.coverNeedSeal and not (
        title_in_text(blob, "封面加盖公章") or title_in_text(blob, "加盖公章")
    ):
        gaps.append(
            _gap(
                "封面公章",
                "招标书要求封面加盖公章，生成稿已留预留字样，打印后请盖章",
                severity=SEVERITY_SUGGEST,
                category="format",
            )
        )
    if fmt.coverShowCopyMark:
        mark = (fmt.coverCopyMark or "正本").strip()
        if mark and not title_in_text(blob, mark, min_len=2):
            gaps.append(
                _gap(
                    mark,
                    "招标书要求封面标明正本/副本，生成稿封面未见该字样",
                    severity=SEVERITY_SUGGEST,
                    category="format",
                )
            )
    tender_no = (brief.tenderNo or "").strip()
    if fmt.coverShowTenderNo and tender_no and not title_in_text(blob, tender_no, min_len=3):
        gaps.append(
            _gap(
                tender_no,
                "招标书要求封面写招标编号，生成稿中未找到该编号",
                severity=SEVERITY_DEDUCT,
                category="format",
            )
        )

    coverage = {
        "outline": _score_bucket(outline_found, outline_total),
        "commercial": _score_bucket(commercial_found, commercial_total),
        "quote": _score_bucket(quote_found, quote_total),
        "technical": _score_bucket(tech_found, tech_total),
        "qualification": _score_bucket(mat_found, mat_total),
    }
    weights = (
        ("outline", 0.30),
        ("qualification", 0.25),
        ("quote", 0.20),
        ("commercial", 0.15),
        ("technical", 0.10),
    )
    weighted = 0.0
    weight_sum = 0.0
    for key, weight in weights:
        bucket = coverage[key]
        if bucket["total"] <= 0:
            continue
        weighted += bucket["score"] * weight
        weight_sum += weight
    rule_score = round(weighted / weight_sum) if weight_sum else 100
    if any(g["severity"] == SEVERITY_DISQUALIFY for g in gaps):
        rule_score = min(rule_score, 72)
    return {"score": rule_score, "coverage": coverage, "missing": gaps}


def parse_qa_payload(raw: str) -> dict[str, Any]:
    data = extract_json_object(raw, prefer_keys=_QA_JSON_KEYS)
    if not isinstance(data, dict):
        raise AppError(ErrorCode.VALIDATION, "质检结果不是 JSON 对象", status_code=422)
    score = data.get("similarityScore", data.get("coverageScore"))
    try:
        similarity = int(float(score))
    except (TypeError, ValueError):
        similarity = 0
    similarity = max(0, min(100, similarity))
    missing: list[dict[str, str]] = []
    raw_missing = data.get("missing")
    if isinstance(raw_missing, list):
        for item in raw_missing:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            if not title:
                continue
            severity = str(item.get("severity") or SEVERITY_SUGGEST).strip()
            if severity not in _SEV_RANK:
                severity = SEVERITY_SUGGEST
            category = str(item.get("category") or "other").strip() or "other"
            missing.append(
                _gap(
                    title,
                    str(item.get("reason") or "").strip(),
                    severity=severity,
                    category=category,
                    source="llm",
                )
            )
    summary = str(data.get("summary") or "").strip()[:400]
    return {"similarityScore": similarity, "summary": summary, "missing": missing}


def merge_gaps(*groups: list[dict[str, str]]) -> list[dict[str, str]]:
    merged: dict[str, dict[str, str]] = {}
    for group in groups:
        for item in group:
            key = compact_text(item.get("title") or "")
            if len(key) < 2:
                continue
            prev = merged.get(key)
            if prev is None:
                merged[key] = dict(item)
                continue
            if _SEV_RANK.get(item.get("severity") or "", 9) < _SEV_RANK.get(
                prev.get("severity") or "", 9
            ):
                prev["severity"] = item["severity"]
            if item.get("reason") and item["reason"] not in (prev.get("reason") or ""):
                prev["reason"] = (prev.get("reason") or "") or item["reason"]
            if prev.get("source") == "llm" and item.get("source") == "rule":
                prev["source"] = "rule"
    ordered = sorted(
        merged.values(),
        key=lambda g: (_SEV_RANK.get(g.get("severity") or "", 9), g.get("title") or ""),
    )
    return ordered


def combine_score(rule_score: int, llm_score: int | None) -> int:
    if llm_score is None:
        return max(0, min(100, int(rule_score)))
    return max(0, min(100, round(0.45 * rule_score + 0.55 * llm_score)))


def grade_for(score: int) -> str:
    if score >= 85:
        return "good"
    if score >= 70:
        return "fair"
    return "risk"


def _grade_label(grade: str) -> str:
    return {
        "good": "响应较完整",
        "fair": "有缺项需核对",
        "risk": "缺项较多，存在废标或扣分风险",
    }.get(grade, "有缺项需核对")


def build_report(
    *,
    record_id: str,
    docx_file: str,
    invitation: str,
    bid_text: str,
    rule: dict[str, Any],
    llm: dict[str, Any] | None,
    source: str = _SOURCE_GENERATED,
    upload_name: str | None = None,
    volume: str | None = None,
) -> dict[str, Any]:
    llm_score = int(llm["similarityScore"]) if llm and "similarityScore" in llm else None
    score = combine_score(int(rule.get("score") or 0), llm_score)
    grade = grade_for(score)
    missing = merge_gaps(list(rule.get("missing") or []), list((llm or {}).get("missing") or []))
    summary = str((llm or {}).get("summary") or "").strip()
    if not summary:
        n = len(missing)
        summary = (
            f"规则核对符合度 {score}%（{_grade_label(grade)}）"
            + (f"，列出 {n} 项缺失" if n else "，未发现明显缺项")
        )
        if not invitation.strip():
            summary = "未保存邀请书原文，仅按组卷大纲与表单做缺项检查。" + summary
    src = source if source in {_SOURCE_GENERATED, _SOURCE_UPLOAD} else _SOURCE_GENERATED
    vol = normalize_qa_volume(volume)
    out = {
        "recordId": record_id,
        "docxFile": docx_file,
        "volume": vol,
        "similarityScore": score,
        "ruleScore": int(rule.get("score") or 0),
        "llmScore": llm_score,
        "grade": grade,
        "gradeLabel": _grade_label(grade),
        "summary": summary,
        "invitationChars": len(invitation or ""),
        "bidChars": len(bid_text or ""),
        "hasInvitation": bool((invitation or "").strip()),
        "llmUsed": llm is not None,
        "coverage": rule.get("coverage") or {},
        "missing": missing,
        "checkedAt": to_epoch_ms(datetime.now(UTC)),
        "source": src,
    }
    if src == _SOURCE_UPLOAD:
        name = (upload_name or "").strip()[:160]
        if name:
            out["uploadName"] = name
    return out


async def extract_bid_text(path: Path) -> str:
    from api.services.knowledge.parsers import extract_text_from_file

    extracted = await extract_text_from_file(path, ext="docx")
    return clip_text((extracted.text or "").strip(), _BID_TEXT_MAX)


async def analyze_with_llm(
    db: AsyncSession,
    *,
    invitation: str,
    bid_text: str,
    brief: BidBrief,
    volume: str | None = None,
) -> dict[str, Any] | None:
    if not (invitation or "").strip() or not (bid_text or "").strip():
        return None
    try:
        from api.services.models.runtime import build_llm_client

        client = await build_llm_client(db, "fast")
        outline_src = brief.outlineItems or []
        vol = (volume or "").strip().lower()
        if vol in QA_VOLUMES:
            from api.services.tenders.outline import items_for_volume

            outline_src = items_for_volume(brief, vol)
        outline = "、".join(
            (item.title or "").strip()
            for item in outline_src
            if (item.title or "").strip() and not item.skipped
        )[:800]
        vol_label = "技术标 Word" if vol == "technical" else "商务标 Word"
        user = "\n".join(
            [
                f"项目：{(brief.projectName or '').strip() or '（未填）'}",
                f"招标人：{(brief.tenderer or '').strip() or '（未填）'}",
                f"当前质检对象：{vol_label}。只评这一卷该有的内容。",
                f"组卷目录：{outline or '（未抽出）'}",
                "",
                "【邀请书正文】",
                clip_text(invitation, _LLM_INVITE_CHARS),
                "",
                "【已生成投标文件正文】",
                clip_text(bid_text, _LLM_BID_CHARS),
            ]
        )
        raw = await client.complete(
            [
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": user},
            ],
            mode="fast",
        )
        return parse_qa_payload(raw)
    except Exception:
        logger.exception("tender qa llm failed project=%s", (brief.projectName or "")[:40])
        return None


async def _missing_slot_titles(
    db: AsyncSession, brief: BidBrief, *, volume: str | None = None
) -> list[str]:
    try:
        from api.services.tenders.library_kb import catalog_placeholders, resolve_attachments
        from api.services.tenders.placeholders import collect_slots, this_bid_keys

        catalog = await catalog_placeholders(db)
        has_agent = bool((brief.agentName or "").strip() or (brief.agentIdNo or "").strip())
        required_keys, include_keys = this_bid_keys(
            extra=brief.extraPlaceholders,
            catalog=catalog,
            required_keys=brief.requiredSlotKeys,
            include_keys=brief.includeSlotKeys,
            has_agent=has_agent,
        )
        slots = collect_slots(brief.extraPlaceholders, catalog=catalog, include_keys=include_keys)
        media = await resolve_attachments(db, slots)
        titles = {item.key: item.title or item.key for item in slots}
        vol = (volume or "").strip().lower()
        from api.services.tenders.categories import slot_volume

        missing: list[str] = []
        for key in required_keys:
            title = titles.get(key) or key
            if vol in QA_VOLUMES and slot_volume(key, title) != vol:
                continue
            files = media.get(key) if isinstance(media, dict) else None
            if not files:
                missing.append(title)
        return missing
    except Exception:
        logger.exception("tender qa slot resolve failed")
        return []


async def inspect_bid(
    db: AsyncSession,
    *,
    public_id: str,
    brief: BidBrief,
    docx_file: str,
    bid_path: Path | None = None,
    source: str = _SOURCE_GENERATED,
    upload_name: str | None = None,
    volume: str | None = None,
) -> dict[str, Any]:
    from api.services.tenders.generate import resolve_output_file

    src = source if source in {_SOURCE_GENERATED, _SOURCE_UPLOAD} else _SOURCE_GENERATED
    vol = normalize_qa_volume(volume)
    if bid_path is not None:
        path = Path(bid_path)
        if not path.is_file():
            raise AppError(ErrorCode.VALIDATION, "上传的 Word 无法读取", status_code=422)
    else:
        path = resolve_output_file(docx_file)
    bid_text = await extract_bid_text(path)
    if len(bid_text) < 20:
        raise AppError(
            ErrorCode.VALIDATION,
            "无法读取投标文件正文，请确认 Word 完好"
            if src == _SOURCE_UPLOAD
            else "无法读取生成稿正文，请确认 Word 文件完好",
            status_code=422,
        )
    invitation = load_invitation_text(brief.invitationId)
    missing_slots = await _missing_slot_titles(db, brief, volume=vol)
    rule = rule_inspect(brief, bid_text, missing_slot_titles=missing_slots, volume=vol)
    llm = await analyze_with_llm(
        db, invitation=invitation, bid_text=bid_text, brief=brief, volume=vol
    )
    report = build_report(
        record_id=public_id,
        docx_file=docx_file,
        invitation=invitation,
        bid_text=bid_text,
        rule=rule,
        llm=llm,
        source=src,
        upload_name=upload_name,
        volume=vol,
    )
    save_qa_report(public_id, report, volume=vol)
    return report
