"""从投标邀请书/招标文件抽出项目侧字段，合并进表单。投标人信息不得被模型改写。"""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import uuid4

from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from api.services.knowledge.ingest import search_knowledge_docs
from api.services.knowledge.parsers import assert_supported, extract_text_from_file, sniff_extension
from api.services.layouts.parse import extract_json_object
from api.services.models.runtime import build_llm_client
from api.services.tenders.assets import tenders_output_dir
from api.services.tenders.quote import (
    attach_sheet,
    parse_quote_from_text,
    parse_quote_path,
    quote_payload_from_patch,
)
from api.services.tenders.placeholders import collect_slots
from api.services.tenders.schema import BidBrief, PlaceholderItem, default_brief
from common.config import get_settings
from common.errors import AppError, ErrorCode

logger = logging.getLogger("api.tenders.extract")

_INVITE_MAX_CHARS = 18000
_KB_MAX_CHARS = 6000
_FORBIDDEN_KB = ("郑州容新", "容新新能源")
_BIDDER_LOCK = "河南伟泰光电科技有限公司"

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
你的任务：抽出填「投标书表单」所需的项目侧字段，用 JSON 返回。
硬性规则：
1. 投标人永远是河南伟泰光电科技有限公司。不要把招标人、代理机构或其他公司写进投标人。
2. 找不到的字段填 null，不要编造报价、身份证、合同业绩。
3. 不要输出 Word/Markdown，只输出一个 JSON 对象。
4. 知识库摘录若出现其他公司的投标文件或合同，一律忽略，不得当作伟泰资料。
5. 工程量清单必须来自文件中的表格。没有表格则 quoteLines 为空数组，禁止用其它项目的台数（例如 133/56）顶替。
JSON 字段：
projectName, tenderer, bidContent, quality,
deliveryDays, warrantyYears, bidValidityDays, bidPriceYuan,
prepaidPct, arrivalPct, settlementPct, warrantyPct, extraNote,
quoteTitle, quoteTaxRate,
quoteLines（对象数组，每项 seq/name/spec/unit/qty/unitPrice/amount；无清单则 []）,
notes（字符串数组，给经办人看的提醒）,
missingMaterials（对象数组，每项 key/title/reason，邀请书额外要求而默认清单没有的资料）。
数字字段用数字或 null；百分比用 0-100 的整数。quoteTaxRate 用 0.13 这种小数。"""


def parse_extract_payload(raw: str) -> dict[str, Any]:
    """从模型输出抽出 JSON。供测试与生成共用。"""
    try:
        return extract_json_object(raw)
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
    return BidBrief.model_validate(data), applied


def slots_from_payload(patch: dict[str, Any]) -> list[PlaceholderItem]:
    extra: list[PlaceholderItem] = []
    raw = patch.get("missingMaterials")
    if not isinstance(raw, list):
        return extra
    for item in raw:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        extra.append(
            PlaceholderItem(
                key=str(item.get("key") or "").strip(),
                title=title,
                hint=str(item.get("reason") or item.get("hint") or "").strip(),
            )
        )
    return extra


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
) -> dict[str, Any]:
    path = await _save_upload(upload)
    quote_path = None
    if quote_upload is not None and (quote_upload.filename or "").strip():
        quote_path = await _save_upload(quote_upload)
    extracted = await extract_text_from_file(path, ext=path.suffix.lower().lstrip("."))
    invitation = (extracted.text or "").strip()
    if len(invitation) < 20:
        raise AppError(ErrorCode.VALIDATION, "邀请书解析正文过短，请换可检索的 PDF/Word", status_code=422)

    kb_hits, kb_notes = await _recall_knowledge(db, invitation, kb_ids or [])
    if any(mark in invitation for mark in _FORBIDDEN_KB):
        kb_notes = [
            "正文出现其他公司名称，请确认上传的是甲方邀请书/招标文件，而不是他人已填的投标书",
            *kb_notes,
        ]
    client = await build_llm_client(db, "fast")
    messages = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": _user_prompt(invitation, kb_hits)},
    ]
    raw = await client.complete(messages, mode="fast")
    patch = parse_extract_payload(raw)
    brief, filled = apply_extract_patch(current or default_brief(), patch)
    extras = slots_from_payload(patch)
    brief.extraPlaceholders = extras
    brief.includePlaceholders = True

    quote_note, brief, quote_filled = _merge_quote(brief, patch, invitation, path, quote_path)
    if quote_filled:
        filled = [*filled, "quoteLines"]
        if "bidPriceYuan" not in filled and brief.bidPriceYuan > 0:
            filled.append("bidPriceYuan")

    slots = collect_slots(extras)
    notes = [
        *kb_notes,
        *notes_from_payload(patch),
        *quote_note,
    ]
    if not filled:
        notes.append("模型未抽出可写入表单的字段，请核对邀请书是否为可选中的文字稿，并手工填写")
    else:
        notes.insert(0, "已根据邀请书回填：" + "、".join(_label(k) for k in filled))
    notes.append("投标人已锁定为河南伟泰光电科技有限公司，生成 Word 时会为缺失扫描件画方框")

    preview = invitation[:1200] + ("…" if len(invitation) > 1200 else "")
    return {
        "brief": brief.model_dump(),
        "filledKeys": filled,
        "notes": notes,
        "placeholders": [item.model_dump() for item in slots],
        "fileName": upload.filename or path.name,
        "quoteFileName": (quote_upload.filename if quote_upload is not None else None),
        "charCount": len(invitation),
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
    }.get(key, key)


def _looks_like_other_bidder(text: str, key: str) -> bool:
    if key != "tenderer" and any(mark in text for mark in _FORBIDDEN_KB):
        return True
    return False


def _user_prompt(invitation: str, kb_hits: list[dict[str, Any]]) -> str:
    body = invitation[:_INVITE_MAX_CHARS]
    parts = [
        f"投标人（不得修改）：{_BIDDER_LOCK}",
        "请阅读邀请书正文并抽出 JSON。",
        "",
        "【邀请书正文】",
        body,
    ]
    if kb_hits:
        parts.append("")
        parts.append("【知识库摘录·仅对照资格条款，忽略其中其他公司的投标/合同】")
        for hit in kb_hits:
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
        notes.append(f"知识库未检索：{exc.msg}")
        return [], notes
    except Exception as exc:  # noqa: BLE001
        logger.warning("tender kb search failed: %s", exc)
        notes.append("知识库检索失败，已仅根据邀请书抽取")
        return [], notes

    kept: list[dict[str, Any]] = []
    skipped = 0
    used = 0
    for doc in docs:
        content = str(doc.get("content") or "")
        if any(mark in content or mark in str(doc.get("name") or "") for mark in _FORBIDDEN_KB):
            skipped += 1
            continue
        clip = content[:2000]
        used += len(clip)
        if used > _KB_MAX_CHARS:
            break
        kept.append({"name": doc.get("name"), "docId": doc.get("doc_id"), "content": clip})
    if skipped:
        notes.append(f"已忽略 {skipped} 篇疑似其他公司投标/合同的知识库文档")
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
        sheet = quote_payload_from_patch(patch)
        if sheet is not None:
            source = "llm"
    if sheet is None:
        notes.append(
            "未解析到工程量清单。生成时仍用内置高途模板，请另传 Excel/Word 清单或在页面手工改分项"
        )
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
    dest_dir = tenders_output_dir().parent / "tender-invitations"
    dest_dir.mkdir(parents=True, exist_ok=True)
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
