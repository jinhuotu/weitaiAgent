"""类似业绩：上传时 OCR/抽取合同关键字段，生成时优先已竣工充电桩项目。"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from api.services.layouts.parse import extract_json_object
from api.services.tenders.schema import PerformanceLine
from common.errors import AppError

logger = logging.getLogger("api.tenders.performance")

PERF_META_PREFIX = "PERFJSON:"
_MAX_OCR_CHARS = 6000
_WEAK_NAME = ("snipaste", "screenshot", "img_", "dsc_", "wechat", "微信", "屏幕", "截图", "图片")
_CHARGER_MARK = ("充电", "充电桩", "充电机", "群充", "直流桩", "交流桩", "箱变")
_ONGOING_MARK = ("在建", "未竣工", "施工中", "供货中", "尚未验收")
_DONE_MARK = ("已竣工", "竣工", "已验收", "验收合格", "已完成", "完工")


def is_weak_title(name: str) -> bool:
    compact = re.sub(r"\s+", "", name or "").lower()
    if not compact:
        return True
    return any(mark in compact for mark in _WEAK_NAME)


def is_charger_line(line: PerformanceLine) -> bool:
    if line.chargerRelated:
        return True
    blob = f"{line.projectName} {line.spec} {line.summary} {line.note}"
    return any(mark in blob for mark in _CHARGER_MARK)


def dump_perf_meta(line: PerformanceLine) -> str:
    return PERF_META_PREFIX + json.dumps(line.model_dump(mode="json"), ensure_ascii=False)


def load_perf_meta(raw: str | None) -> PerformanceLine | None:
    text = (raw or "").strip()
    if not text.startswith(PERF_META_PREFIX):
        return None
    try:
        payload = json.loads(text[len(PERF_META_PREFIX) :])
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return performance_from_dict(payload)


def performance_from_dict(
    item: dict[str, Any],
    *,
    allow_client_title: bool = True,
) -> PerformanceLine | None:
    name = str(item.get("projectName") or item.get("name") or "").strip()
    client = str(item.get("client") or item.get("buyer") or "").strip()
    if is_weak_title(name):
        name = ""
    if not name and client and allow_client_title:
        name = f"{client}充电桩供货合同"[:255]
    if not name:
        return None
    amount = _amount_from_value(item.get("amountYuan") or item.get("amount"))
    ongoing = bool(item.get("ongoing"))
    if item.get("completed") is True:
        ongoing = False
    charger = item.get("chargerRelated")
    if charger is None:
        charger = any(mark in f"{name} {item.get('spec') or ''} {item.get('summary') or ''}" for mark in _CHARGER_MARK)
    contact = str(item.get("contact") or "").strip()
    if contact in {"无", "没有", "暂无"}:
        contact = ""
    return PerformanceLine(
        projectName=name[:255],
        spec=str(item.get("spec") or "").strip()[:255],
        location=str(item.get("location") or "").strip()[:255],
        client=client[:255],
        contact=contact[:128] or ("保密" if item.get("contactHidden") else ""),
        amountYuan=max(0.0, amount),
        summary=str(item.get("summary") or "").strip()[:800],
        note=str(item.get("note") or "").strip()[:400],
        ongoing=ongoing,
        chargerRelated=bool(charger),
    )


def _amount_from_value(raw: object) -> float:
    if raw is None or raw == "":
        return 0.0
    text = str(raw).replace(",", "").replace("￥", "").replace("¥", "").strip()
    try:
        if text.endswith("万元") or text.endswith("万"):
            return float(text.replace("万元", "").replace("万", "") or 0) * 10000
        return float(text.replace("元", "") or 0)
    except (TypeError, ValueError):
        return 0.0


_AMOUNT_RE = re.compile(r"(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>万元|万|元)")


def amount_from_text(text: str) -> float:
    match = _AMOUNT_RE.search(text or "")
    if not match:
        return 0.0
    try:
        num = float(match.group("num"))
    except ValueError:
        return 0.0
    unit = match.group("unit")
    if unit in {"万", "万元"}:
        return num * 10000
    return num


def fallback_from_text(text: str, filename: str) -> PerformanceLine | None:
    blob = f"{filename}\n{text or ''}"
    client = ""
    for pat in (r"甲方(?:[（(]?全称[）)]?)?[：:]\s*([^\n]{2,40})", r"买方[：:]\s*([^\n]{2,40})", r"发包人[：:]\s*([^\n]{2,40})"):
        found = re.search(pat, blob)
        if found:
            client = re.sub(r"\s+", "", found.group(1)).strip("，,。；; ")
            break
    project = ""
    for pat in (r"(?:工程名称|项目名称|合同名称)[：:]\s*([^\n]{2,60})",):
        found = re.search(pat, blob)
        if found:
            project = re.sub(r"\s+", "", found.group(1)).strip("，,。；; ")
            break
    if is_weak_title(project):
        project = ""
    if not project and client:
        project = f"{client}充电桩供货合同"
    if not project:
        return None
    ongoing = any(mark in blob for mark in _ONGOING_MARK) and not any(mark in blob for mark in _DONE_MARK)
    spec = ""
    kw = re.search(r"(\d+\s*[kK][wW])", blob)
    if kw:
        spec = kw.group(1).replace(" ", "")
    else:
        for mark in ("直流", "交流", "群充", "箱变"):
            if mark in blob:
                spec = mark
                break
    return PerformanceLine(
        projectName=project[:255],
        spec=spec[:255],
        client=client[:255],
        amountYuan=amount_from_text(blob),
        summary="资料库合同/发票扫描件",
        ongoing=ongoing,
        chargerRelated=any(mark in blob for mark in _CHARGER_MARK),
    )


def rank_performance_lines(lines: list[PerformanceLine]) -> list[PerformanceLine]:
    """招标优先已竣工充电桩，其次其他已完成，再在建。"""
    usable = [item for item in lines if (item.projectName or "").strip() and not is_weak_title(item.projectName)]

    def key(item: PerformanceLine) -> tuple[int, int, float]:
        charger = 1 if is_charger_line(item) else 0
        done = 0 if item.ongoing else 1
        return (done, charger, float(item.amountYuan or 0))

    return sorted(usable, key=key, reverse=True)


async def extract_performance_from_path(
    db: AsyncSession,
    path: Path,
    *,
    filename: str,
) -> PerformanceLine | None:
    text = ""
    try:
        from api.services.knowledge.parsers import extract_text_from_file

        extracted = await extract_text_from_file(path, ext=path.suffix.lower().lstrip("."))
        text = (extracted.text or "")[:_MAX_OCR_CHARS]
    except AppError:
        logger.info("performance OCR skipped file=%s", filename)
    except Exception:
        logger.exception("performance OCR failed file=%s", filename)

    line = await _extract_with_llm(db, text, filename) if text.strip() else None
    if line is None:
        line = fallback_from_text(text, filename)
    return line


async def _extract_with_llm(db: AsyncSession, text: str, filename: str) -> PerformanceLine | None:
    try:
        from api.services.models.runtime import build_llm_client

        client = await build_llm_client(db, "fast")
        raw = await client.complete(
            [
                {
                    "role": "system",
                    "content": (
                        "你从河南伟泰光电科技有限公司的合同或发票扫描识别文本中抽取一条投标业绩。"
                        "只填文本里能确认的字段，禁止编造。只输出一个 JSON 对象。"
                        "字段：projectName（工程/合同名称）, spec（充电桩规格，如直流功率、群充、箱变）,"
                        "client（买方/甲方）, contact（甲方联系人，没有则空字符串）,"
                        "amountYuan（合同额，单位元，数字）, summary（概况：直流桩台数、场站类型、EPC或设备供货）,"
                        "ongoing（true=在建未竣工，false=已竣工完成）, chargerRelated（是否充电桩相关）,"
                        "note（补充）。找不到的填空字符串或 false。"
                    ),
                },
                {
                    "role": "user",
                    "content": f"文件名：{filename}\n正文：\n{text[:_MAX_OCR_CHARS]}",
                },
            ],
            mode="fast",
        )
        payload = extract_json_object(raw)
        if not isinstance(payload, dict):
            return None
        return performance_from_dict(payload)
    except Exception:
        logger.exception("performance LLM extract failed file=%s", filename)
        return None
