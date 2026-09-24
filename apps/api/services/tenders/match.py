"""本标附件与资料库按 key 匹配：不对上就标缺，绝不拿其他项的扫描件顶。"""

from __future__ import annotations

import re
from typing import Any

from api.services.tenders.placeholders import normalize_slot_keys
from api.services.tenders.schema import PlaceholderItem

_COMPACT = re.compile(r"[\s/（）()【】\[\]:：·,，。、\-—_]+")
# 邀请书常写全称，资料库是短名；同族才复用，避免拿身份证去顶信用截图。
_KIND_PHRASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "credit",
        (
            "信用中国",
            "信用信息公示",
            "企业信用查询",
            "失信查询",
            "信用截图",
            "信用查询",
            "无不良记录截图",
        ),
    ),
    ("id_legal", ("法定代表人身份证", "法人身份证", "法人代表身份证")),
    (
        "id_agent",
        (
            "授权代理人身份证",
            "委托代理人身份证",
            "授权委托人身份证",
            "委托人身份证",
            "被授权人身份证",
        ),
    ),
    ("license", ("营业执照",)),
    ("bank_permit", ("开户许可", "开户许可证", "基本账户", "基本户")),
    ("iso", ("iso", "质量体系", "管理体系认证", "体系认证证书", "体系证书", "资质证书")),
    ("safety", ("安全生产许可证", "安全生产许可")),
    ("bond", ("投标保证金", "保证金缴存", "保证金回单")),
    ("finance", ("财务审计", "审计报告", "完税证明", "财务报表", "财务报告")),
    ("social", ("社保缴纳", "社保缴费")),
    ("perf", ("类似业绩", "类似项目", "合同及发票", "业绩证明", "业绩合同")),
    ("product", ("检测报告", "型式试验", "3c认证", "ccc认证", "3c", "桩型证明", "产品合格证")),
    ("seal", ("签章页", "盖章页")),
    ("commitment", ("承诺书", "承诺函", "无违法", "无行贿")),
)


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


def _kind_hits(text: str) -> set[str]:
    n = compact_title(text)
    if len(n) < 4:
        return set()
    # 「身份证明」含「身份证」三字，不能当成证件扫描件。
    n = n.replace("身份证明", "")
    if len(n) < 4:
        return set()
    hits: set[str] = set()
    for kind, phrases in _KIND_PHRASES:
        for raw in phrases:
            p = compact_title(raw)
            if p and p in n:
                hits.add(kind)
                break
    return hits


def _same_material_kind(left: str, right: str) -> bool:
    return bool(_kind_hits(left) & _kind_hits(right))


def scan_slot_key(title: str, slots: list[PlaceholderItem] | None = None) -> str:
    hits = _kind_hits(title)
    if not hits:
        return ""
    for item in slots or []:
        key = (item.key or "").strip()
        if key and (key in hits or _same_material_kind(title, item.title or "")):
            return key
    if "license" in hits:
        return "license"
    if len(hits) == 1:
        return next(iter(hits))
    return ""


def _item_blob(item: PlaceholderItem) -> str:
    return f"{item.title or ''} {item.hint or ''}".strip()


def find_catalog_item(
    catalog: list[PlaceholderItem],
    *,
    key: str = "",
    title: str = "",
    hint: str = "",
) -> PlaceholderItem | None:
    want = (key or "").strip()
    query = f"{title} {hint}".strip()
    q_hits = _kind_hits(query)
    if want and q_hits and want not in q_hits:
        want = ""
    if want:
        for item in catalog:
            if item.key == want:
                return item
    if not compact_title(query):
        return None
    for item in catalog:
        if _titles_loosely_match(title, item.title):
            i_hits = _kind_hits(item.title)
            if q_hits and i_hits and not (q_hits & i_hits):
                continue
            return item
    for item in catalog:
        blob = _item_blob(item)
        i_hits = _kind_hits(blob)
        if q_hits and i_hits and not (q_hits & i_hits):
            continue
        if title and (
            _titles_loosely_match(title, item.hint or "") or _titles_loosely_match(title, blob)
        ):
            return item
        if hint and _titles_loosely_match(hint, item.title):
            return item
        if _same_material_kind(query, blob):
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
            found = find_catalog_item(catalog_items, key=key, title=title, hint=reason)
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
            found = find_catalog_item(catalog_items, key=key, title=title, hint=reason)
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
