"""商务标 / 技术标：资格项废标，技术方案扣分。"""

from __future__ import annotations

from api.services.tenders.placeholders import TECH_DRAWING_KEY
from api.services.tenders.schema import BidBrief, PlaceholderItem

BUSINESS_SLOT_KEYS = frozenset(
    {
        "id_legal",
        "id_agent",
        "perf",
        "finance",
        "credit",
        "bond",
        "seal",
        "license",
        "bank_permit",
        "iso",
        "commitment",
    }
)
TECHNICAL_SLOT_KEYS = frozenset({"product", TECH_DRAWING_KEY})
HIGH_DISQUALIFY_KEYS = frozenset({"id_legal", "id_agent", "bond", "seal", "finance", "credit"})

LEGACY_TECH_FIELDS: tuple[str, ...] = (
    "constructionPlan",
    "layoutPlan",
    "powerPlan",
    "omPlan",
    "schedulePlan",
)
TECH_CHAPTERS: tuple[tuple[str, str, str], ...] = (
    ("techPlanNote", "技术方案说明", "按本邀请书各技术章起草，缺事实标待补充，不要编造数量"),
)

PENDING_DRAFT = "【待响应】请按招标文件补充本章响应，缺事实处勿编造。"
PENDING_FACT = "【待补充】"
TECH_CHAPTER_KINDS = frozenset({"tech_plan", "tech_chapter"})

_TECH_TITLE_MARK = (
    "方案",
    "组织设计",
    "质量",
    "安全",
    "进度",
    "措施",
    "保证体系",
    "技术规格",
    "图纸",
    "工艺",
    "信息化",
    "风险",
    "环保",
    "售后",
    "培训",
    "验收",
    "调试",
    "运维",
    "偏离",
    "偏差",
)
_TECH_KINDS = frozenset({"tech_dev", "tech_plan", "tech_chapter"})
_BIZ_KINDS = frozenset(
    {"letter", "legal_id", "auth", "quote", "biz_dev", "commitment_copy", "performance", "factory", "company"}
)
_VOLUME_LABELS = frozenset({"商务标", "技术标", "商务部分", "技术部分", "商务标书", "技术标书"})
# 资格词先于「质量 / 安全 / 进度」，避免质量保证金进技术卷。
_BIZ_TITLE_CUE = (
    "保证金",
    "资格",
    "营业执照",
    "执照",
    "资质",
    "财务",
    "信用",
    "承诺函",
    "承诺书",
    "授权",
    "投标函",
    "商务偏离",
    "商务偏差",
    "业绩",
)
_TECH_TITLE_CUE = (
    "技术标准",
    "技术参数",
    "技术方案",
    "施工组织",
    "施工方案",
    "实施方案",
    "技术偏离",
    "技术偏差",
)


def _title_cue(title: str) -> str:
    n = "".join((title or "").split())
    if not n:
        return ""
    if any(k in n for k in _BIZ_TITLE_CUE):
        return "business"
    if title_goes_technical(n) or any(k in n for k in _TECH_TITLE_CUE):
        return "technical"
    return ""


def slot_volume(key: str, title: str = "") -> str:
    if key in TECHNICAL_SLOT_KEYS:
        return "technical"
    if key in BUSINESS_SLOT_KEYS:
        return "business"
    cue = _title_cue(title)
    if cue:
        return cue
    blob = f"{key}{title}"
    if any(mark in blob for mark in _TECH_TITLE_MARK):
        return "technical"
    return "business"


def is_volume_label(title: str) -> bool:
    n = "".join((title or "").split())
    n = n.replace("投标文件", "").replace("响应文件", "")
    return n in _VOLUME_LABELS


def item_volume(*, kind: str = "", title: str = "", key: str = "") -> str:
    kind = (kind or "").strip()
    if kind in _TECH_KINDS:
        return "technical"
    # 函/报价/商务偏离等已定则不改卷，避免动商务标生成。
    if kind in _BIZ_KINDS:
        return "business"
    cue = _title_cue(title)
    if cue:
        return cue
    if title_goes_technical(title):
        return "technical"
    return slot_volume(key, title)


def title_goes_technical(title: str) -> bool:
    n = "".join((title or "").split())
    if not n:
        return False
    if "发包人代表" in n:
        return True
    if "撤换" in n and "发包人" in n:
        return True
    return False


def is_high_disqualify(key: str) -> bool:
    return key in HIGH_DISQUALIFY_KEYS


def is_tech_chapter_kind(kind: str) -> bool:
    return (kind or "").strip() in TECH_CHAPTER_KINDS


def tech_outline_items(brief: BidBrief):
    return [
        item
        for item in (brief.outlineItems or [])
        if not item.skipped and is_tech_chapter_kind(item.kind)
    ]


def tech_plan_text(brief: BidBrief) -> str:
    drafts = [
        str(getattr(item, "draftBody", "") or "").strip()
        for item in tech_outline_items(brief)
        if str(getattr(item, "draftBody", "") or "").strip()
    ]
    if drafts:
        return "\n\n".join(drafts)
    note = str(getattr(brief, "techPlanNote", "") or "").strip()
    if note:
        return note
    parts = [str(getattr(brief, field, "") or "").strip() for field in LEGACY_TECH_FIELDS]
    return "\n\n".join(part for part in parts if part)


def chapter_draft_text(item, brief: BidBrief) -> str:
    draft = str(getattr(item, "draftBody", "") or "").strip()
    if draft:
        return draft
    note = tech_plan_text(brief)
    if note:
        items = tech_outline_items(brief)
        if len(items) <= 1:
            return note
        n = "".join((getattr(item, "title", "") or "").split())
        if "实施方案" in n or "技术方案" in n or n.endswith("技术标"):
            return note
    return PENDING_DRAFT


def tech_plan_body(brief: BidBrief) -> str:
    text = tech_plan_text(brief)
    if text:
        return text
    return PENDING_DRAFT


def technical_soft_issues(
    brief: BidBrief,
    *,
    slots: list[PlaceholderItem] | None = None,
    media: dict | None = None,
    required_keys: list[str] | None = None,
) -> list[str]:
    """技术标缺项：生成时写入警告，不阻止出 Word。"""
    issues: list[str] = []
    if not any((row.requirement or "").strip() for row in (brief.deviationLines or [])):
        issues.append("技术标：尚未填写技术偏差表，未响应招标技术要求会大量扣分")
    empty_chaps = [
        item.title
        for item in tech_outline_items(brief)
        if not str(getattr(item, "draftBody", "") or "").strip()
    ]
    if empty_chaps:
        issues.append(f"技术标：{len(empty_chaps)} 章尚未起草（{empty_chaps[0]}）")
    elif not tech_plan_text(brief):
        issues.append("技术标：技术方案尚未起草")
    drawing_files = media.get(TECH_DRAWING_KEY) or [] if isinstance(media, dict) else []
    if not drawing_files:
        issues.append("技术标：尚未上传本项目图纸")
    from api.services.tenders.performance import (
        bid_performance_lines,
        is_perf_placeholder,
        performance_match_issues,
        requirement_active,
    )

    selected = bid_performance_lines(brief.performanceLines or [])
    real = [row for row in selected if not is_perf_placeholder(row)]
    if requirement_active(brief.performanceRequirement):
        if not real:
            issues.append("商务标：类似业绩未从资料库匹配到，已按招标要求占位")
        else:
            issues.extend(performance_match_issues(brief.performanceLines, brief.performanceRequirement))
    required = set(required_keys or [])
    for item in slots or []:
        if slot_volume(item.key, item.title) != "technical":
            continue
        if item.key in required or item.key == TECH_DRAWING_KEY:
            continue
        files = media.get(item.key) if isinstance(media, dict) else None
        if not files:
            issues.append(f"技术标：{item.title or item.key}未上传，可能扣分")
    return issues
