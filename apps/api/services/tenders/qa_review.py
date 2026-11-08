"""人工核验状态的独立持久化逻辑。

只操作 QA report 的 attentionItems.review 字段，不参与生成、匹配或评分。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from api.services.tenders.qa import compact_text

REVIEW_STATUSES = {"pending", "confirmed", "rectified"}


def review_key(item: dict[str, Any]) -> str:
    return "|".join(
        (
            str(item.get("kind") or "").strip(),
            compact_text(str(item.get("requirement") or "")),
        )
    )


def preserve_reviews(previous: dict[str, Any] | None, report: dict[str, Any]) -> dict[str, Any]:
    """Copy prior review decisions to matching newly extracted items."""
    old_items = (previous or {}).get("attentionItems") or []
    old_by_key = {
        review_key(item): item.get("review")
        for item in old_items
        if isinstance(item, dict) and isinstance(item.get("review"), dict)
    }
    for item in report.get("attentionItems") or []:
        prior = old_by_key.get(review_key(item))
        if prior:
            item["review"] = dict(prior)
    return report


def update_review(
    report: dict[str, Any],
    item_id: str,
    *,
    status: str,
    owner: str = "",
    note: str = "",
    confirmed_by: str = "",
) -> tuple[dict[str, Any], bool]:
    """Update one review and report whether the report changed."""
    if status not in REVIEW_STATUSES:
        raise ValueError("status 必须是 pending、confirmed 或 rectified")
    for item in report.get("attentionItems") or []:
        if item.get("id") != item_id:
            continue
        old = item.get("review") or {}
        new = {
            "status": status,
            "owner": owner.strip(),
            "note": note.strip(),
            "confirmedAt": datetime.now(UTC).isoformat(),
            "confirmedBy": confirmed_by.strip(),
        }
        stable_fields = ("status", "owner", "note", "confirmedBy")
        if all(old.get(key, "") == new.get(key, "") for key in stable_fields):
            return report, False
        item["review"] = new
        return report, True
    raise KeyError(f"未找到规则项：{item_id}")
