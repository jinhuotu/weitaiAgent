"""类似业绩：上传时 OCR/抽取合同关键字段，按本标招标门槛筛选。"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from api.services.layouts.parse import extract_json_object
from api.services.tenders.schema import PerformanceLine, PerformanceRequirement
from common.errors import AppError

logger = logging.getLogger("api.tenders.performance")

PERF_META_PREFIX = "PERFJSON:"
_MAX_OCR_CHARS = 6000
_WEAK_NAME = ("snipaste", "screenshot", "img_", "dsc_", "wechat", "微信", "屏幕", "截图", "图片")
_CHARGER_MARK = ("充电", "充电桩", "充电机", "群充", "直流桩", "交流桩", "箱变")
_ONGOING_MARK = ("在建", "未竣工", "施工中", "供货中", "尚未验收")
_DONE_MARK = ("已竣工", "竣工", "已验收", "验收合格", "已完成", "完工")


@dataclass
class PerformanceMatchResult:
    passed: bool
    amount_ok: bool = True
    similar_ok: bool = True
    completed_ok: bool = True
    reasons: list[str] = field(default_factory=list)


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
    data = line.model_dump(mode="json")
    data.pop("includeInBid", None)
    return PERF_META_PREFIX + json.dumps(data, ensure_ascii=False)


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
        name = f"{client}项目合同"[:255]
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
        project = f"{client}项目合同"
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


def rank_performance_lines(
    lines: list[PerformanceLine],
    requirement: PerformanceRequirement | None = None,
) -> list[PerformanceLine]:
    """有招标门槛时符合项在前；否则已竣工优先，不再默认充电桩。"""
    usable = [item for item in lines if (item.projectName or "").strip() and not is_weak_title(item.projectName)]
    active = requirement_active(requirement)

    def key(item: PerformanceLine) -> tuple[int, int, float]:
        matched = 1 if active and match_performance_line(item, requirement).passed else 0
        done = 0 if item.ongoing else 1
        return (matched, done, float(item.amountYuan or 0))

    return sorted(usable, key=key, reverse=True)


def line_name_key(name: str) -> str:
    return re.sub(r"\s+", "", name or "").lower()


def bid_performance_lines(lines: list[PerformanceLine] | None) -> list[PerformanceLine]:
    """本标勾选写入 Word / 附件的业绩行。"""
    out: list[PerformanceLine] = []
    for item in lines or []:
        if not (item.projectName or "").strip() or is_weak_title(item.projectName):
            continue
        if not bool(getattr(item, "includeInBid", True)):
            continue
        out.append(item)
    return out


def apply_include_in_bid(
    lines: list[PerformanceLine],
    requirement: PerformanceRequirement | None,
    *,
    preserve: dict[str, bool] | None = None,
) -> list[PerformanceLine]:
    """有招标门槛时默认只勾选符合项；preserve 保留用户已勾选/取消的行。"""
    held = preserve or {}
    active = requirement_active(requirement)
    for item in lines:
        key = line_name_key(item.projectName)
        if key in held:
            item.includeInBid = held[key]
        elif active:
            item.includeInBid = match_performance_line(item, requirement).passed
        else:
            item.includeInBid = True
    return lines


_PERF_ANCHOR = re.compile(
    r"类似项目|类似业绩|同类项目|同类工程|合同业绩|企业业绩|"
    r"近三年.{0,12}(?:业绩|类似)|业绩证明|类似的项目"
)
_SCOPE_WORDS = (
    "MES",
    "MOM",
    "WMS",
    "TPM",
    "QMS",
    "数字化工厂",
    "数智化",
    "智能制造",
    "制造执行",
    "制造运营",
    "充电桩",
    "充电设施",
    "充电机",
    "直流桩",
    "交流桩",
    "群充",
    "热力",
    "供热",
    "供暖",
    "信息化",
    "系统集成",
    "软件开发",
    "DICT",
    "电缆",
    "箱变",
    "光伏",
    "变电站",
    "电力施工",
    "EPC",
)
_CHARGER_SCOPE = frozenset({"充电桩", "充电设施", "充电机", "直流桩", "交流桩", "群充"})
_CN_COUNT = {
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}
_AMOUNT_NEAR = re.compile(
    r"(?:单份|单项|单个合同|每份合同|每份|合同金额|金额|签约合同价)[^。；;]{0,18}"
    r"(?:不少于|不低于|达到|须达到|应达到|以上|≥|>=|>＝)"
    r"(?:人民币)?"
    r"(?P<num>\d+(?:\.\d+)?)(?P<unit>万元|万|元)"
)
_AMOUNT_BEFORE = re.compile(
    r"(?:不少于|不低于)(?:人民币)?(?P<num>\d+(?:\.\d+)?)(?P<unit>万元|万)"
    r"[^。；;]{0,12}(?:类似|同类|合同)"
)
_COUNT_PAT = re.compile(
    r"(?:至少|不少于)(?:提供)?(?P<n>\d+|[一二三四五六七八九十两])(?:个|项|份)"
    r"|提供(?P<n2>\d+|[一二三四五六七八九十两])(?:个|项|份).{0,8}(?:类似|同类)"
)
_SCOPE_PAT = re.compile(r"(?:类似|同类)(?:的)?(?P<scope>[^，。；;、]{2,16}?)(?:项目|工程|合同|业绩)")


def requirement_active(req: PerformanceRequirement | None) -> bool:
    if req is None:
        return False
    return bool(
        (req.minAmountYuan or 0) > 0
        or (req.minCount or 0) > 0
        or req.requireCompleted
        or (req.keywords or [])
        or (req.similarScope or "").strip()
    )


def empty_requirement() -> PerformanceRequirement:
    return PerformanceRequirement()


def _word_in(blob: str, word: str) -> bool:
    if not word or not blob:
        return False
    if word in blob:
        return True
    return bool(word.isascii() and len(word) >= 2 and word.upper() in blob.upper())


def extract_performance_requirement(text: str) -> PerformanceRequirement:
    """从招标/邀请书抽出业绩门槛。找不到则空对象，不套用默认 20 万。"""
    windows = _perf_windows(text)
    if not windows:
        return empty_requirement()
    blob = "".join(windows)
    amount = _amount_from_windows(windows)
    count = _count_from_blob(blob)
    keywords = [word for word in _SCOPE_WORDS if _word_in(blob, word)]
    scopes = []
    for window in windows:
        for match in _SCOPE_PAT.finditer(window):
            scope = (match.group("scope") or "").strip("的 ")
            if scope and scope not in {"项目", "工程", "合同"} and len(scope) <= 16:
                scopes.append(scope)
    similar = scopes[0] if scopes else ("、".join(keywords[:3]) if keywords else "")
    if similar:
        for word in _SCOPE_WORDS:
            if _word_in(similar, word) and word not in keywords:
                keywords.append(word)
    if count <= 0:
        count = 3
    require_completed = any(mark in blob for mark in ("已竣工", "已完成", "竣工验收", "已验收", "近三年完成"))
    if any(mark in blob for mark in ("在建亦可", "含在建", "在建项目也可")):
        require_completed = False
    note = windows[0][:120]
    return PerformanceRequirement(
        similarScope=similar[:80],
        keywords=keywords[:8],
        minAmountYuan=amount,
        minCount=count,
        requireCompleted=require_completed,
        note=note,
    )


def requirement_from_payload(patch: dict[str, Any] | None) -> PerformanceRequirement:
    raw = (patch or {}).get("performanceRequirement")
    if not isinstance(raw, dict):
        return empty_requirement()
    amount = _amount_from_value(raw.get("minAmountYuan") or raw.get("minAmount"))
    try:
        count = int(raw.get("minCount") or 0)
    except (TypeError, ValueError):
        count = 0
    keywords = [
        str(item).strip()
        for item in (raw.get("keywords") or [])
        if isinstance(item, str) and str(item).strip()
    ]
    similar = str(raw.get("similarScope") or raw.get("scope") or "").strip()[:80]
    for word in _SCOPE_WORDS:
        if word in similar and word not in keywords:
            keywords.append(word)
    return PerformanceRequirement(
        similarScope=similar,
        keywords=keywords[:8],
        minAmountYuan=max(0.0, amount),
        minCount=max(0, min(count, 20)),
        requireCompleted=bool(raw.get("requireCompleted")),
        note=str(raw.get("note") or "").strip()[:160],
    )


def merge_performance_requirement(
    rule: PerformanceRequirement | None,
    llm: PerformanceRequirement | None,
    *,
    invitation: str = "",
) -> PerformanceRequirement:
    left = rule or empty_requirement()
    right = llm or empty_requirement()
    compact_inv = re.sub(r"\s+", "", invitation or "")
    amount = left.minAmountYuan or 0
    if amount <= 0 and right.minAmountYuan > 0 and _amount_mentioned(compact_inv, right.minAmountYuan):
        amount = right.minAmountYuan
    count = left.minCount or 0
    if count <= 0 and 0 < right.minCount <= 20:
        count = right.minCount
    keywords = list(dict.fromkeys([*(left.keywords or []), *(right.keywords or [])]))[:8]
    similar = (left.similarScope or "").strip() or (right.similarScope or "").strip()
    if not similar and keywords:
        similar = "、".join(keywords[:3])
    note = (left.note or "").strip() or (right.note or "").strip()
    return PerformanceRequirement(
        similarScope=similar[:80],
        keywords=keywords[:8],
        minAmountYuan=amount,
        minCount=count,
        requireCompleted=bool(left.requireCompleted or right.requireCompleted),
        note=note[:160],
    )


def match_performance_line(
    line: PerformanceLine,
    requirement: PerformanceRequirement | None,
) -> PerformanceMatchResult:
    req = requirement or empty_requirement()
    if not requirement_active(req):
        return PerformanceMatchResult(passed=True, amount_ok=True, similar_ok=True, completed_ok=True, reasons=[])
    blob = compact_perf_text(line)
    amount_ok = True
    similar_ok = True
    completed_ok = True
    reasons: list[str] = []
    if req.minAmountYuan > 0:
        if float(line.amountYuan or 0) <= 0:
            amount_ok = False
            reasons.append("金额未识别")
        elif float(line.amountYuan or 0) + 0.5 < req.minAmountYuan:
            amount_ok = False
            reasons.append("金额不足")
    if req.keywords or (req.similarScope or "").strip():
        similar_ok = _similar_hit(line, blob, req)
        if not similar_ok:
            reasons.append("类型不符")
    if req.requireCompleted and line.ongoing:
        completed_ok = False
        reasons.append("在建")
    return PerformanceMatchResult(
        passed=amount_ok and similar_ok and completed_ok,
        amount_ok=amount_ok,
        similar_ok=similar_ok,
        completed_ok=completed_ok,
        reasons=reasons,
    )


def format_requirement(req: PerformanceRequirement | None) -> str:
    if not requirement_active(req):
        return ""
    assert req is not None
    parts: list[str] = []
    scope = (req.similarScope or "").strip() or "、".join(req.keywords[:3])
    if scope:
        parts.append(f"同类「{scope}」")
    if req.minAmountYuan > 0:
        parts.append(f"单份≥{_amount_label(req.minAmountYuan)}")
    if req.minCount > 0:
        parts.append(f"至少{req.minCount}个")
    if req.requireCompleted:
        parts.append("须已竣工")
    return "，".join(parts)


def performance_match_issues(
    lines: list[PerformanceLine],
    requirement: PerformanceRequirement | None,
) -> list[str]:
    if not requirement_active(requirement):
        return []
    assert requirement is not None
    selected = bid_performance_lines(lines)
    listed = [
        item
        for item in (lines or [])
        if (item.projectName or "").strip() and not is_weak_title(item.projectName)
    ]
    passed = sum(1 for item in selected if match_performance_line(item, requirement).passed)
    label = format_requirement(requirement)
    if not listed:
        return [f"类似业绩为空，招标要求：{label}"]
    if not selected:
        return [f"类似业绩未勾选写入本标，招标要求：{label}"]
    if requirement.minCount > 0 and passed < requirement.minCount:
        return [f"类似业绩符合招标要求 {passed} 条，招标要求至少 {requirement.minCount} 个（{label}）"]
    if passed == 0:
        return [f"类似业绩均未达到招标要求（{label}）"]
    return []


def compact_perf_text(line: PerformanceLine) -> str:
    return re.sub(
        r"\s+",
        "",
        f"{line.projectName}{line.spec}{line.summary}{line.note}{line.client}",
    )


def _similar_hit(line: PerformanceLine, blob: str, req: PerformanceRequirement) -> bool:
    for word in req.keywords or []:
        if _word_in(blob, word):
            return True
        if word in _CHARGER_SCOPE and is_charger_line(line):
            return True
    scope = re.sub(r"\s+", "", req.similarScope or "")
    if len(scope) >= 2 and (scope in blob or _word_in(blob, scope)):
        return True
    return False


def _perf_windows(text: str) -> list[str]:
    raw = text or ""
    windows: list[str] = []
    for match in _PERF_ANCHOR.finditer(raw):
        start = max(0, match.start() - 80)
        end = min(len(raw), match.end() + 260)
        chunk = re.sub(r"\s+", "", raw[start:end])
        if "保证金" in chunk and not any(mark in chunk for mark in ("类似", "业绩", "同类")):
            continue
        if chunk and chunk not in windows:
            windows.append(chunk)
    return windows[:6]


def _amount_from_windows(windows: list[str]) -> float:
    singles: list[float] = []
    others: list[float] = []
    for window in windows:
        for pat in (_AMOUNT_NEAR, _AMOUNT_BEFORE):
            for match in pat.finditer(window):
                left = window[max(0, match.start() - 16) : match.start()]
                mid = window[max(0, match.start() - 8) : match.end() + 8]
                if "累计" in mid or "保证金" in left:
                    continue
                value = _amount_from_value(f"{match.group('num')}{match.group('unit')}")
                if value <= 0:
                    continue
                if "单份" in left or "每份" in left or "单项" in left:
                    singles.append(value)
                else:
                    others.append(value)
    if singles:
        return max(singles)
    return max(others) if others else 0.0


def _count_from_blob(blob: str) -> int:
    match = _COUNT_PAT.search(blob or "")
    if not match:
        return 0
    raw = match.group("n") or match.group("n2") or ""
    if raw.isdigit():
        return max(0, min(int(raw), 20))
    return _CN_COUNT.get(raw, 0)


def _amount_mentioned(compact_inv: str, amount: float) -> bool:
    if amount <= 0 or not compact_inv:
        return False
    as_int = str(int(round(amount)))
    wan = amount / 10000.0
    wan_int = str(int(round(wan))) if abs(wan - round(wan)) < 0.05 else ""
    return as_int in compact_inv or (wan_int and f"{wan_int}万" in compact_inv)


def _amount_label(amount: float) -> str:
    if amount >= 10000 and abs(amount / 10000 - round(amount / 10000)) < 0.005:
        return f"{int(round(amount / 10000))}万元"
    if amount >= 10000:
        compact = f"{amount / 10000:.2f}".rstrip("0").rstrip(".")
        return f"{compact}万元"
    return f"{int(amount)}元"


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
