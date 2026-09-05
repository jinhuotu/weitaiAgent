"""本公司历史投标：只保留组卷经验，剥掉项目名、报价、台数和他司内容。"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.services.tenders.categories import tech_plan_text
from api.services.tenders.placeholders import DEFAULT_SLOTS, normalize_slot_keys
from api.services.tenders.schema import BidBrief, COMPANY_FIELD_KEYS
from db.models.tender import TenderRecord

BIDDER_LOCK = "河南伟泰光电科技有限公司"
FOREIGN_BIDDER_MARKS = ("郑州容新", "容新新能源")
_HISTORY_LIMIT = 5

_SLOT_TITLE = {item.key: item.title for item in DEFAULT_SLOTS if item.key}


@dataclass(frozen=True)
class HistoryPlaybook:
    """剥数字后的组卷经验，可进入识别提示，不可进入报价/业绩。"""

    slot_keys: tuple[str, ...] = ()
    factory_role: str = ""
    tech_written: tuple[str, ...] = ()
    tech_missing: tuple[str, ...] = ()


def contains_foreign_bidder(text: str) -> bool:
    blob = text or ""
    return any(mark in blob for mark in FOREIGN_BIDDER_MARKS)


def sanitize_history_brief(raw: BidBrief | dict | None) -> HistoryPlaybook | None:
    """丢掉项目名、招标人、报价、台数、业绩正文，只留附件 key 与技术标是否写过。"""
    if raw is None:
        return None
    try:
        brief = raw if isinstance(raw, BidBrief) else BidBrief.model_validate(raw)
    except Exception:
        return None
    bidder = (brief.bidderName or "").strip()
    if bidder and BIDDER_LOCK not in bidder:
        return None
    if contains_foreign_bidder(bidder) or contains_foreign_bidder(brief.factoryRole or ""):
        return None

    keys = normalize_slot_keys(
        [
            *(brief.requiredSlotKeys or []),
            *(brief.includeSlotKeys or []),
            *(item.key for item in (brief.extraPlaceholders or []) if item.key),
        ]
    )
    role = (brief.factoryRole or "").strip()[:80]
    if contains_foreign_bidder(role):
        role = ""

    written: list[str] = []
    missing: list[str] = []
    if tech_plan_text(brief):
        written.append("实施方案说明")
    else:
        missing.append("实施方案说明")

    if not keys and not role and not written:
        return None
    return HistoryPlaybook(
        slot_keys=tuple(keys),
        factory_role=role,
        tech_written=tuple(written),
        tech_missing=tuple(missing),
    )


def playbooks_prompt_block(playbooks: list[HistoryPlaybook] | None) -> str:
    """给识别模型的对照段。不含项目名、报价、台数。"""
    items = [item for item in (playbooks or []) if item.slot_keys or item.factory_role or item.tech_written]
    if not items:
        return ""
    lines = [
        "【本公司历史组卷经验·仅对照附件种类与技术标是否要写】",
        "禁止抄项目名、招标人、报价、工程量台数、合同业绩。quoteLines 必须为 []。",
    ]
    seen_keys: set[str] = set()
    merged_keys: list[str] = []
    roles: list[str] = []
    written: set[str] = set()
    for book in items:
        for key in book.slot_keys:
            if key in seen_keys:
                continue
            seen_keys.add(key)
            merged_keys.append(key)
        if book.factory_role and book.factory_role not in roles:
            roles.append(book.factory_role)
        written.update(book.tech_written)
    if merged_keys:
        labeled = [f"{key}（{_SLOT_TITLE.get(key, key)}）" if key in _SLOT_TITLE else key for key in merged_keys]
        lines.append("近期常用附件 key：" + "、".join(labeled))
    if roles:
        lines.append("原厂角色口径：" + "、".join(roles))
    if written:
        lines.append("技术标曾写过：" + "、".join(written))
    return "\n".join(lines)


def playbook_note(playbooks: list[HistoryPlaybook] | None) -> str:
    items = playbooks or []
    keys: list[str] = []
    seen: set[str] = set()
    for book in items:
        for key in book.slot_keys:
            if key in seen:
                continue
            seen.add(key)
            keys.append(_SLOT_TITLE.get(key, key))
    if not keys:
        return ""
    return "本公司近期组卷常用附件：" + "、".join(keys) + "（仅供对照，未自动勾选，工程量仍以本邀请书表格为准）"


async def latest_company_profile(db: AsyncSession, *, limit: int = 8) -> dict[str, str]:
    """最近一次本公司投标里的公司/法人字段，供新邀请书空项回填。"""
    stmt = (
        select(TenderRecord)
        .where(TenderRecord.brief_json.is_not(None))
        .order_by(TenderRecord.id.desc())
        .limit(max(1, min(limit, 20)))
    )
    rows = (await db.execute(stmt)).scalars().all()
    for row in rows:
        raw = row.brief_json if isinstance(row.brief_json, dict) else {}
        name = str(raw.get("bidderName") or "").strip()
        if name and BIDDER_LOCK not in name:
            continue
        if contains_foreign_bidder(name) or contains_foreign_bidder(str(raw.get("legalPersonName") or "")):
            continue
        profile: dict[str, str] = {}
        for key in COMPANY_FIELD_KEYS:
            text = str(raw.get(key) or "").strip()
            if text:
                profile[key] = text
        legal = (row.legal_person_name or "").strip()
        if legal and not profile.get("legalPersonName"):
            profile["legalPersonName"] = legal
        if not profile:
            continue
        profile["bidderName"] = BIDDER_LOCK
        return profile
    return {}


async def load_history_playbooks(db: AsyncSession, *, limit: int = _HISTORY_LIMIT) -> list[HistoryPlaybook]:
    stmt = (
        select(TenderRecord)
        .where(TenderRecord.brief_json.is_not(None))
        .order_by(TenderRecord.id.desc())
        .limit(max(1, min(limit, 20)))
    )
    rows = (await db.execute(stmt)).scalars().all()
    out: list[HistoryPlaybook] = []
    fingerprints: set[tuple[str, ...]] = set()
    for row in rows:
        book = sanitize_history_brief(row.brief_json if isinstance(row.brief_json, dict) else None)
        if book is None:
            continue
        fp = book.slot_keys
        if fp in fingerprints:
            continue
        fingerprints.add(fp)
        out.append(book)
    return out
