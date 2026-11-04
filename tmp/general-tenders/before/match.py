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
    ("social", ("社保缴纳", "社保缴费", "社保证明")),
    ("perf", ("类似业绩", "类似项目", "合同及发票", "业绩证明", "业绩合同")),
    ("product", ("检测报告", "型式试验", "3c认证", "ccc认证", "3c", "桩型证明", "产品合格证")),
    ("seal", ("签章页", "盖章页")),
    ("commitment", ("承诺书", "承诺函", "无违法", "无行贿")),
)


def compact_title(text: str) -> str:
    return _COMPACT.sub("", text or "").lower()


_ATTACH_CUE_START = r"(?:本表后应附|本表后附|后应附|应在此提供|在此提供|在此附|应提供|需提供|须提供|应附|须附|需附|附[：:]|附(?!录|件|注|表))"
_ATTACH_CUE = re.compile(
    _ATTACH_CUE_START + r"([^。；;]*?)(?=(?:[，,][^，,。；;]{0,24})?" + _ATTACH_CUE_START + r"|[。；;]|$)"
)
_ATTACH_NAMES = (
    ("license", "营业执照", r"营业执照"),
    ("qualification", "资质证书副本", r"(?:施工资质|企业资质|投标人资质|资质证书)"),
    ("safety", "安全生产许可证", r"安全生产许可(?:证)?"),
    ("bond", "投标保证金汇款凭证", r"汇款凭证|保证金(?:回单|缴存凭证)"),
    ("bank_permit", "基本账户开户证明", r"基本账户(?:开户证明|证明)|开户许可证"),
    ("bank_guarantee", "银行保函", r"银行保函"),
    ("finance", "财务审计报告及财务报表", r"审计报告|财务报表|财务会计报表"),
    ("perf", "中标通知书或合同协议书", r"中标通知书|合同协议书"),
)


def attachment_requests(text: str) -> list[PlaceholderItem]:
    """只从附具说明提取资料；同一说明的不同证件分别落位，或选材料保留为一项。"""
    flat = re.sub(r"\s+", "", text or "")
    result: dict[str, PlaceholderItem] = {}
    for cue in _ATTACH_CUE.finditer(flat):
        clause = cue.group(1)
        for key, title, pattern in _ATTACH_NAMES:
            if re.search(pattern, clause):
                if key == "perf":
                    title = "或".join(word for word in ("中标通知书", "合同协议书") if word in clause)
                elif key == "finance":
                    title = "及".join(word for word in ("审计报告", "财务会计报表", "财务报表") if word in clause)
                result.setdefault(key, PlaceholderItem(key=key, title=title, hint=cue.group(0)))
        # 未列举的材料仍按说明生成有名称的框，不把任意「提供」要求当扫描件。
        if not re.search(r"复印件|扫描件|截图|证明材料|(?:证书|许可证|报告|报表)(?:[（(].*?[）)])?$", clause):
            continue
        raw = re.sub(r"(?:的)?(?:复印件或扫描件|复印件|扫描件|原件)", "", clause)
        if "包括" in raw and re.search(r"财务会计报表|财务报表", raw):
            raw = raw.split("包括", 1)[0].rstrip("，,")
        for part in re.split(r"[、，,]|以及|和", raw):
            if any(re.search(pattern, part) for _, _, pattern in _ATTACH_NAMES):
                continue
            name = re.sub(r"^(?:及|包括|有效的?|相关的?|经审计的|成立以来的)", "", part)
            name = re.sub(r"(?:等材料|等资料|等)$", "", name).strip("：:")
            name = re.sub(r"^满足第.+规定的(?:相关)?证明材料$", "相关证明材料", name)
            optional = re.match(r"^((?:学历证|职称证|注册资格证书)[（(]如有[）)])", name)
            if optional:
                name = optional.group(1)
            if not 2 <= len(name) <= 32 or name.startswith(("若", "具体", "投标人应")) or re.search(r"应附|须附|需附|提供|岗位名称|毕业时间|第二章|第[0-9.]+项", name):
                continue
            if len(name) > 16 and not re.search(r"许可证|证书|证明|报告|报表|状况表|协议书|通知书|合同|截图|材料|发票|保险|承诺书|学历证|职称证|身份证", name):
                continue
            key = "request_" + compact_title(name)
            result.setdefault(key, PlaceholderItem(key=key, title=name, hint=cue.group(0)))
    return list(result.values())


def attachment_catalog_item(request: PlaceholderItem, catalog: list[PlaceholderItem]) -> PlaceholderItem | None:
    """按资料名称匹配，施工资质不能用 ISO 体系证书替代，保函不能用汇款回单替代。"""
    if request.title == "相关证明材料":
        return None
    if request.key in {"qualification", "bank_guarantee", "bond"}:
        aliases = {
            "qualification": r"施工资质|企业资质|资质证书|投标人资质",
            "bank_guarantee": r"银行保函",
            "bond": r"汇款凭证|保证金.*(?:回单|缴存|汇款)",
        }
        return next((item for item in catalog if re.search(aliases[request.key], item.title or "")
                     and not (request.key == "qualification" and re.search(r"iso|体系", item.title or "", re.I))), None)
    if request.key == "perf":
        return next((item for item in catalog if re.search(r"类似.*(?:业绩|合同|项目)|合同协议书|中标通知书", item.title or "")), None)
    if request.key == "finance":
        return find_catalog_item(catalog, title="财务审计报告")
    return find_catalog_item(catalog, title=request.title)


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


# 这些类型互斥：不能因「社保」等弱标签把身份证和审计报告算一类
_EXCLUSIVE_KINDS = frozenset(
    {
        "id_legal",
        "id_agent",
        "credit",
        "commitment",
        "bond",
        "license",
        "bank_permit",
        "finance",
        "perf",
        "product",
        "seal",
    }
)
_WEAK_KINDS = frozenset({"social"})


def _same_material_kind(left: str, right: str) -> bool:
    ha, hb = _kind_hits(left), _kind_hits(right)
    if not ha or not hb:
        return False
    shared = ha & hb
    if not shared:
        return False
    if shared <= _WEAK_KINDS:
        return False
    ea, eb = ha & _EXCLUSIVE_KINDS, hb & _EXCLUSIVE_KINDS
    if ea and eb and ea != eb:
        return False
    return True


def same_library_material(left: str, right: str) -> bool:
    if _same_material_kind(left, right):
        return True
    la, lb = _kind_hits(left), _kind_hits(right)
    ea, eb = la & _EXCLUSIVE_KINDS, lb & _EXCLUSIVE_KINDS
    if ea and eb and ea != eb:
        return False
    return _titles_loosely_match(left, right)


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


def _clean_attach_section(raw) -> str:
    text = str(raw or "").strip()
    n = "".join(text.split())
    for bad in ("投标函", "目录", "目次", "法定代表人身份证明"):
        if bad in n and len(n) <= len(bad) + 8:
            return ""
    return text[:80]


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
            section = _clean_attach_section(raw.get("attachToSection"))
            found = find_catalog_item(catalog_items, key=key, title=title, hint=reason)
            if found is not None:
                add_mapped(
                    PlaceholderItem(
                        key=found.key,
                        title=found.title,
                        hint=reason or found.hint,
                        attachToSection=section,
                    )
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


def _filled_same_kind(row: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    title = str(row.get("title") or "")
    hits = _kind_hits(title)
    if not hits:
        return None
    want = str(row.get("key") or "")
    for other in rows:
        ok = str(other.get("key") or "")
        if not ok or ok == want or int(other.get("fileCount") or 0) <= 0:
            continue
        if _kind_hits(str(other.get("title") or "")) & hits:
            return other
        if _titles_loosely_match(title, str(other.get("title") or "")):
            return other
    return None


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
        nfile = int(row.get("fileCount") or 0)
        if nfile <= 0:
            alias = _filled_same_kind(row, catalog_rows)
            if alias is not None:
                ak = str(alias.get("key") or "")
                if ak and ak not in seen:
                    seen.add(ak)
                    matched.append(
                        {
                            "key": ak,
                            "title": str(alias.get("title") or ak),
                            "fileCount": int(alias.get("fileCount") or 0),
                            "required": key in required or ak in required,
                            "created": False,
                        }
                    )
                continue
        item = {
            "key": key,
            "title": str(row.get("title") or key),
            "fileCount": nfile,
            "required": key in required,
            "created": key in created,
        }
        if nfile > 0:
            matched.append({**item, "created": False})
        elif key in created:
            created_items.append(item)
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
