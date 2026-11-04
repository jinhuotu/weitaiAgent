"""演示开关：不读邀请书、不组卷，下载事先放好的 Word。"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import shutil
from pathlib import Path
from uuid import uuid4

from common.config import get_settings
from common.errors import AppError, ErrorCode

logger = logging.getLogger("api.tenders.demo")

# 目录跟这份邀请书格式章的目录走，按本标时只显示这些。
_OUTLINE = (
    ("o01", "投标函及投标函附录", "letter"),
    ("o02", "法定代表人身份证明", "legal_id"),
    ("o03", "授权委托书", "auth"),
    ("o04", "投标保证金", "company"),
    ("o05", "已标价的工程量清单", "quote"),
    ("o06", "项目管理机构", "company"),
    ("o07", "项目管理机构组成表", "company"),
    ("o08", "项目经理简历表", "company"),
    ("o09", "项目经理主要工作经历", "company"),
    ("o10", "承诺书", "commitment_copy"),
    ("o11", "主要项目管理人员简历表", "company"),
    ("o12", "拟分包项目情况表", "company"),
    ("o13", "资格审查资料", "company"),
    ("o14", "投标人基本情况表", "company"),
    ("o15", "近年财务状况表", "company"),
    ("o16", "近年完成的类似项目情况表", "performance"),
    ("o17", "正在施工的和新承接的项目情况表", "performance"),
    ("o18", "“国家企业信用信息公示系统”网站查询", "scan"),
    ("o19", "资格审查其他材料", "scan"),
    ("o20", "其他资料", "company"),
    ("o21", "施工组织设计", "tech_plan"),
)

# 同一文件名固定落到同一份稿；对不上关键字时，按上传顺序把两份邀请书对上两个范本。
_picked: dict[str, str] = {}


async def _pause(seconds: float) -> None:
    await asyncio.sleep(seconds)


def demo_enabled() -> bool:
    raw = str(getattr(get_settings(), "tender_demo", "") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def demo_root() -> Path:
    root = Path(get_settings().storage_root).expanduser().resolve() / "tender-demo"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _cases() -> list[dict]:
    path = demo_root() / "manifest.json"
    if not path.is_file():
        raise AppError(
            ErrorCode.VALIDATION,
            "演示稿清单不存在：storage/tender-demo/manifest.json",
            status_code=422,
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise AppError(ErrorCode.VALIDATION, "演示稿清单不是合法 JSON", status_code=422) from exc
    rows = data.get("cases") if isinstance(data, dict) else None
    if not isinstance(rows, list) or not rows:
        raise AppError(ErrorCode.VALIDATION, "演示稿清单里没有 cases", status_code=422)
    return [row for row in rows if isinstance(row, dict)]


def _match_keys(row: dict) -> list[str]:
    keys = row.get("match") or []
    if isinstance(keys, str):
        keys = [keys]
    return [str(key or "").strip().lower() for key in keys if str(key or "").strip()]


def _remember(name: str, row: dict) -> dict:
    if name:
        _picked[name] = str(row.get("id") or "")
    return row


def _case_by_name(filename: str) -> dict:
    name = (filename or "").strip().lower()
    rows = _cases()
    if name and name in _picked:
        want = _picked[name]
        for row in rows:
            if str(row.get("id") or "") == want:
                return row
    for row in rows:
        if name and any(token in name for token in _match_keys(row)):
            return _remember(name, row)
    used = set(_picked.values())
    for row in rows:
        cid = str(row.get("id") or "")
        if name and cid not in used:
            return _remember(name, row)
    return _remember(name, rows[0])


def _case_by_id(invitation_id: str) -> dict:
    want = (invitation_id or "").strip().lower()
    rows = _cases()
    for row in rows:
        if str(row.get("id") or "").strip().lower() == want:
            return row
    return rows[0]


def _prepared(case: dict, key: str) -> Path | None:
    rel = str(case.get(key) or "").strip().replace("\\", "/")
    if not rel:
        return None
    root = demo_root()
    path = (root / rel).resolve()
    if not path.is_relative_to(root):
        raise AppError(ErrorCode.VALIDATION, "演示稿路径无效", status_code=422)
    return path


def _require_docx(path: Path | None, label: str) -> Path:
    if path is None or not path.is_file():
        shown = str(path) if path is not None else label
        raise AppError(
            ErrorCode.VALIDATION,
            f"演示成稿还没放好：{shown}",
            status_code=422,
        )
    return path


def _brief_for(case: dict) -> dict:
    from api.services.tenders.schema import BidBrief, QuoteLineIn

    brief = BidBrief()
    data = brief.model_dump()
    for key in (
        "projectName",
        "tenderer",
        "tenderNo",
        "bidContent",
        "quality",
        "deliveryDays",
        "warrantyYears",
        "bidValidityDays",
        "bidPriceYuan",
        "controlPriceYuan",
        "pmName",
        "outlineChapter",
        "layoutMode",
    ):
        if case.get(key) not in (None, ""):
            data[key] = case[key]
    data["invitationId"] = str(case.get("id") or "").strip()
    lines = case.get("quoteLines")
    if isinstance(lines, list) and lines:
        data["quoteLines"] = lines
        data["quoteTitle"] = str(case.get("quoteTitle") or "工程量清单")
        data["quoteSource"] = "sheet"
    elif not data.get("quoteLines"):
        data["quoteLines"] = [
            QuoteLineIn(seq="1", name="充电桩设备及安装", unit="项", qty=1, unitPrice=1, amount=1).model_dump()
        ]
        data["quoteTitle"] = "工程量清单"
        data["quoteSource"] = "sheet"
    if not data.get("outlineItems"):
        data["outlineItems"] = [
            {
                "id": item_id,
                "title": title,
                "rawTitle": title,
                "kind": kind,
                "source": "copy",
            }
            for item_id, title, kind in _OUTLINE
        ]
    data["outlineChapter"] = data.get("outlineChapter") or "投标文件格式"
    checked = BidBrief.model_validate(data)
    return checked.model_dump(mode="json")


async def demo_parse(filename: str) -> dict:
    # 识别故意停 3～5 分钟，再回到填商务标 / 技术标
    await _pause(random.randint(180, 300))
    case = _case_by_name(filename)
    brief = _brief_for(case)
    logger.info("demo parse file=%s case=%s", filename, case.get("id"))
    notes = [
        "已根据邀请书回填：项目名称、招标人、招标编号、投标内容",
        f"已抽出组卷大纲 {len(brief.get('outlineItems') or [])} 条（{brief.get('outlineChapter') or '投标文件格式'}）",
    ]
    return {
        "brief": brief,
        "filledKeys": ["projectName", "tenderer", "tenderNo", "bidContent"],
        "notes": notes,
        "placeholders": [],
        "requiredSlotKeys": [],
        "includeSlotKeys": [],
        "attachmentMatch": {"matched": [], "missingFiles": [], "createdItems": []},
        "fileName": filename or "邀请书.pdf",
        "quoteFileName": None,
        "charCount": 120000,
        "pageCount": 36,
        "ocrPages": 0,
        "ocrCapped": False,
        "ocrMaxPages": 0,
        "preview": (brief.get("projectName") or "")[:200],
        "knowledgeHits": [],
        "agentHint": {},
    }


async def build_demo_output(brief) -> dict:
    await _pause(1.1)
    case = _case_by_id(getattr(brief, "invitationId", "") or "")
    vol = (getattr(brief, "generateVolume", None) or "").strip()
    key = "technical" if vol == "technical" else "business"
    src = _require_docx(_prepared(case, key), key)
    from api.services.tenders.assets import tenders_output_dir
    from api.services.tenders.generate import safe_download_name

    stem = uuid4().hex[:12]
    dest = tenders_output_dir() / f"{stem}.docx"
    shutil.copyfile(src, dest)
    logger.info("demo generate %s -> %s", src.name, dest.name)
    out: dict = {
        "docxFile": None,
        "techDocxFile": None,
        "pdfFile": None,
        "downloadName": "",
        "techDownloadName": None,
        "pdfDownloadName": None,
        "warnings": ["已按邀请书组卷"],
        "defaultsUsed": getattr(brief, "bidderName", "") or "",
        "generateVolume": vol if vol in {"business", "technical", "full"} else "business",
    }
    if key == "technical":
        out["techDocxFile"] = dest.name
        out["techDownloadName"] = safe_download_name(brief.projectName, suffix="技术标.docx")
    elif vol == "full":
        out["docxFile"] = dest.name
        out["downloadName"] = safe_download_name(brief.projectName, suffix="投标文件.docx")
    else:
        out["docxFile"] = dest.name
        out["downloadName"] = safe_download_name(brief.projectName, suffix="商务标.docx")
    return out


def demo_format_file(invitation_id: str) -> Path:
    case = _case_by_id(invitation_id)
    src = _require_docx(_prepared(case, "format"), "format.docx")
    from api.services.tenders.assets import tenders_output_dir

    dest = tenders_output_dir() / f"{invitation_id}.format.docx"
    shutil.copyfile(src, dest)
    return dest
