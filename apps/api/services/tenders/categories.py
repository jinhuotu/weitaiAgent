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
    ("techPlanNote", "实施方案说明", "按本邀请书概括施工、布置、配电、运维与工期，不要编造桩数"),
)

_TECH_TITLE_MARK = ("方案", "布置", "配电", "运维", "图纸", "检测", "3C", "技术规格")
_TECH_KINDS = frozenset({"tech_dev", "tech_plan"})
_BIZ_KINDS = frozenset(
    {"letter", "legal_id", "auth", "quote", "biz_dev", "commitment_copy", "performance", "factory", "company"}
)
_VOLUME_LABELS = frozenset({"商务标", "技术标", "商务部分", "技术部分", "商务标书", "技术标书"})


def slot_volume(key: str, title: str = "") -> str:
    if key in TECHNICAL_SLOT_KEYS:
        return "technical"
    if key in BUSINESS_SLOT_KEYS:
        return "business"
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
    if kind in _BIZ_KINDS:
        return "business"
    return slot_volume(key, title)


def is_high_disqualify(key: str) -> bool:
    return key in HIGH_DISQUALIFY_KEYS


def tech_plan_text(brief: BidBrief) -> str:
    """新字段优先；旧五段方案合并，便于载入历史记录。"""
    note = str(getattr(brief, "techPlanNote", "") or "").strip()
    if note:
        return note
    parts = [str(getattr(brief, field, "") or "").strip() for field in LEGACY_TECH_FIELDS]
    return "\n\n".join(part for part in parts if part)


def tech_plan_body(brief: BidBrief) -> str:
    text = tech_plan_text(brief)
    if text:
        return text
    return "【待响应】请按招标文件补充实施方案文字说明，并上传本项目图纸。"


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
    if not tech_plan_text(brief):
        issues.append("技术标：实施方案文字描述未写（请按邀请书概括，不要编造桩数）")
    drawing_files = media.get(TECH_DRAWING_KEY) or [] if isinstance(media, dict) else []
    if not drawing_files:
        issues.append("技术标：尚未上传实施方案图纸")
    from api.services.tenders.performance import bid_performance_lines, performance_match_issues

    if not any((row.projectName or "").strip() for row in bid_performance_lines(brief.performanceLines or [])):
        issues.append("商务标：类似业绩为空，资格评审可能扣分")
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
