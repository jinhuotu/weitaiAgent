"""本标附件与资料库按 key 匹配：不对上就标缺，绝不拿其他项的扫描件顶。"""

from __future__ import annotations

import re
from typing import Any

from api.services.tenders.placeholders import normalize_slot_keys
from api.services.tenders.schema import PlaceholderItem

_COMPACT = re.compile(r"[\s/（）()【】\[\]:：·,，。、\-—_]+")


def compact_title(text: str) -> str:
    return _COMPACT.sub("", text or "").lower()


def _titles_loosely_match(left: str, right: str) -> bool:
    x, y = compact_title(left), compact_title(right)
    if len(x) < 4 or len(y) < 4:
        return False
    if x == y or x in y or y in x:
        return True
    shorter, longer = (x, y) if len(x) <= len(y) else (y, x)
    if len(shorter) < 6:
        return False
    i = 0
    for ch in longer:
        if i < len(shorter) and ch == shorter[i]:
            i += 1
    return i == len(shorter)


def find_catalog_item(
    catalog: list[PlaceholderItem],
    *,
    key: str = "",
    title: str = "",
) -> PlaceholderItem | None:
    want = (key or "").strip()
    if want:
        for item in catalog:
            if item.key == want:
                return item
    if not compact_title(title):
        return None
    for item in catalog:
        if _titles_loosely_match(title, item.title):
            return item
    return None


def split_invitation_materials(
    patch: dict[str, Any],
    catalog: list[PlaceholderItem] | None,
) -> tuple[list[PlaceholderItem], list[PlaceholderItem]]:
    """requiredMaterials 必须落在资料库 key；对不上的进 missing，禁止借用其他 key 的文件。"""
    catalog_items = [item for item in (catalog or []) if item.key]
    mapped: list[PlaceholderItem] = []
    missing: list[PlaceholderItem] = []
    seen: set[str] = set()

    def add_mapped(item: PlaceholderItem) -> None:
        if not item.key or item.key in seen:
            return
        seen.add(item.key)
        mapped.append(item)

    required = patch.get("requiredMaterials")
    if isinstance(required, list):
        for raw in required:
            if not isinstance(raw, dict):
                continue
            key = str(raw.get("key") or "").strip()
            title = str(raw.get("title") or "").strip()
            reason = str(raw.get("reason") or raw.get("hint") or "").strip()
            found = find_catalog_item(catalog_items, key=key, title=title)
            if found is not None:
                add_mapped(
                    PlaceholderItem(key=found.key, title=found.title, hint=reason or found.hint)
                )
                continue
            label = title or reason
            if len(compact_title(label)) >= 4:
                missing.append(PlaceholderItem(key="", title=label, hint=reason or "邀请书要求，资料库尚无此项"))

    raw_missing = patch.get("missingMaterials")
    if isinstance(raw_missing, list):
        for raw in raw_missing:
            if not isinstance(raw, dict):
                continue
            title = str(raw.get("title") or "").strip()
            if not title:
                continue
            key = str(raw.get("key") or "").strip()
            reason = str(raw.get("reason") or raw.get("hint") or "").strip()
            found = find_catalog_item(catalog_items, key=key, title=title)
            if found is not None:
                add_mapped(
                    PlaceholderItem(key=found.key, title=found.title, hint=reason or found.hint)
                )
                continue
            missing.append(
                PlaceholderItem(
                    key="",
                    title=title,
                    hint=reason or "邀请书额外要求，请在资料库上传，勿用其他项目扫描件顶替",
                )
            )
    return mapped, missing


def slots_from_materials(
    mapped: list[PlaceholderItem],
    missing: list[PlaceholderItem],
) -> list[PlaceholderItem]:
    return [*mapped, *missing]


def build_attachment_match(
    catalog_rows: list[dict[str, Any]],
    *,
    required_keys: list[str] | None = None,
    include_keys: list[str] | None = None,
    created_keys: list[str] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    by_key = {str(row.get("key") or ""): row for row in catalog_rows if row.get("key")}
    required = set(normalize_slot_keys(required_keys))
    include = normalize_slot_keys(include_keys) or list(required)
    created = set(normalize_slot_keys(created_keys))
    matched: list[dict[str, Any]] = []
    missing_files: list[dict[str, Any]] = []
    created_items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for key in include:
        row = by_key.get(key)
        if row is None or key in seen:
            continue
        seen.add(key)
        item = {
            "key": key,
            "title": str(row.get("title") or key),
            "fileCount": int(row.get("fileCount") or 0),
            "required": key in required,
            "created": key in created,
        }
        if key in created:
            created_items.append(item)
        elif item["fileCount"] > 0:
            matched.append(item)
        else:
            missing_files.append(item)
    return {
        "matched": matched,
        "missingFiles": missing_files,
        "createdItems": created_items,
    }


def attachment_match_notes(report: dict[str, Any] | None) -> list[str]:
    if not report:
        return []
    notes: list[str] = []
    matched = [str(x.get("title") or x.get("key") or "") for x in (report.get("matched") or [])]
    missing = [x for x in (report.get("missingFiles") or [])]
    created = [str(x.get("title") or x.get("key") or "") for x in (report.get("createdItems") or [])]
    if matched:
        notes.append(f"已匹配资料库扫描件 {len(matched)} 项")
    req_empty = [
        str(x.get("title") or x.get("key") or "")
        for x in missing
        if x.get("required")
    ]
    opt_empty = [
        str(x.get("title") or x.get("key") or "")
        for x in missing
        if not x.get("required")
    ]
    if req_empty:
        notes.append(
            "未上传（虚线框占位，不会用其他项目文件顶替）：" + "、".join(t for t in req_empty if t)
        )
    if opt_empty:
        notes.append("已纳入本标但未上传：" + "、".join(t for t in opt_empty if t))
    if created:
        notes.append("资料库新建空项请上传：" + "、".join(t for t in created if t))
    if not matched and not missing and not created:
        notes.append("邀请书未抽出资料清单，已默认必填法定代表人身份证。可在下方勾选资料库其他项纳入本标。")
    return notes
