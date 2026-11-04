"""招标书行分类：组卷章节 vs 投标要求。规则优先，不调 LLM。"""

from __future__ import annotations

import re
from dataclasses import dataclass

from api.services.tenders.schema import BidBrief, BidRequirement, PerformanceRequirement

_PREFIX = re.compile(
    r"^(?:"
    r"附(?:件)?[（(]?[一二三四五六七八九十0-9]+[)）]?"
    r"|[（(][一二三四五六七八九十0-9]+[)）]"
    r"|[一二三四五六七八九十]{1,2}、"
    r"|[0-9]{1,2}[.．、]"
    r")[、.．:：\s]*"
)

_CHAPTER_ENDS = (
    "函",
    "表",
    "清单",
    "书",
    "证明",
    "简历",
    "执照",
    "保函",
    "方案",
    "承诺",
    "保证金",
    "业绩",
    "模板",
)
_STD_MARKS = (
    "项目管理机构",
    "拟分包",
    "投标人基本情况",
    "项目经理",
    "发包人代表",
    "联合体",
    "在施工",
    "财务状况",
    "功能需求",
    "报价单模板",
)
_QUAL_MARKS = (
    "独立法人",
    "法人资格",
    "责令停业",
    "暂扣",
    "吊销执照",
    "吊销资质",
    "营业执照处于有效",
    "依法取得企业营业执照",
    "本次招标要求投标人",
)
_PERF_MARKS = (
    "类似项目是指",
    "业绩考核",
    "佐证资料",
    "不得超过本次招标规模",
    "招标人提出业绩",
    "招标人可参考",
)
_SCORE_MARKS = (
    "符合工程情况",
    "具有针对性",
    "评分标准",
    "评审因素",
    "评分办法",
    "计分规则",
    "计分办法",
    "评审标准",
)
_EVAL_TABLE_MARKS = (
    "计分规则",
    "计分办法",
    "评分细则",
)
_MATERIAL_MARKS = (
    "应附",
    "复印件或扫描件",
    "证明材料复印件",
    "满足本章第",
)
_NOTICE_MARKS = (
    "具体如下",
    "可参考下述",
    "进行明确",
)
_NOISE_MARKS = (
    "招标须知",
    "投标须知",
    "投标人须知",
    "供应商须知",
    "采购须知",
    "操作手册",
    "电子保函操作",
    "公共资源交易",
    "指定的接收人",
    "发包人指定",
    "承包人指定",
    "监理人指定",
    "账号",
    "账户",
    "开户行",
    "汇款",
    "评标结束",
    "退还保证金",
    "视为无效",
    "资格审查资料",
    "中小企业声明",
    "残疾人福利",
    "暗标",
    "装订要求",
    "使用说明",
    "示范文本",
    "空格标示",
    "空格标识",
    "目录使用说明",
)
_KIND_LABELS = {
    "scan": "资质扫描件",
    "performance": "业绩",
    "tech_plan": "技术标",
    "quote": "报价",
    "letter": "函件",
    "commitment_copy": "承诺函",
    "company": "企业信息表",
}


@dataclass(frozen=True)
class ContentHit:
    role: str  # chapter | requirement | noise
    category: str = ""
    target_kind: str = ""
    target_title: str = ""


def compact_line(text: str) -> str:
    return re.sub(r"[\s/／|｜]+", "", text or "")


def classify_procurement_type(text: str, titles: list[str] | None = None) -> str:
    """Classify the technical response route without changing outline classification."""
    sample = compact_line("\n".join([*(titles or []), (text or "")[:20_000]]))
    for label, kind in (("货物类", "goods"), ("服务类", "service"), ("工程类", "engineering")):
        if label in sample:
            return kind
    signals = {
        "goods": (
            ("参数表", 4), ("技术参数", 3), ("设备型号", 3),
            ("配置清单", 3), ("货物采购", 4), ("供货范围", 2),
        ),
        "service": (
            ("服务方案", 4), ("服务质量", 3), ("服务响应", 3),
            ("运维服务", 4), ("服务团队", 3), ("应急预案", 1),
        ),
        "engineering": (
            ("施工组织设计", 5), ("施工部署", 4), ("施工方案", 3),
            ("工程量清单", 3), ("施工图纸", 3), ("文明施工", 2),
        ),
    }
    scores = {
        kind: sum(weight for cue, weight in cues if cue in sample)
        for kind, cues in signals.items()
    }
    winner = max(scores, key=scores.get)
    return winner if scores[winner] > 0 else "engineering"


def strip_item_prefix(text: str) -> str:
    s = (text or "").strip().strip("·•-—_ ")
    s = re.sub(r"^\d+(?:\.\d+)+[、.．:：\s]*", "", s)
    return _PREFIX.sub("", s).strip(" :：.．、；;。")


def classify_content(title: str) -> ContentHit:
    raw = (title or "").strip()
    if _is_invite_blank(raw):
        return ContentHit("noise", "notice")
    n = compact_line(strip_item_prefix(raw) or raw)
    if not n:
        return ContentHit("noise", "notice")
    if _is_noise(n, raw):
        return ContentHit("noise", "notice")
    if _is_eval_table(n):
        return ContentHit("noise", "notice")
    if _is_scoring(n):
        return ContentHit("requirement", "scoring", "tech_plan")
    if _is_material(n):
        return ContentHit("requirement", "material", _material_kind(n), _quoted_table(n))
    if _is_qual(n):
        return ContentHit("requirement", "qualification", "scan")
    if _is_perf(n):
        return ContentHit("requirement", "performance", "performance")
    if _is_notice_req(n):
        return ContentHit("requirement", "notice", "performance" if "业绩" in n else "")
    if _looks_like_chapter(n):
        return ContentHit("chapter")
    if len(n) > 22:
        return ContentHit("requirement", "notice")
    return ContentHit("chapter")


def is_chapter_title(title: str) -> bool:
    return classify_content(title).role == "chapter"


# 三级条款标记：mandatory（★/※/实质性）、important（▲/重要性）、general（其余）
_MANDATORY_MARKS = ("★", "※", "废标", "否决", "实质性", "无效投标", "不予受理")
_IMPORTANT_MARKS = ("▲", "重要性", "重要技术参数", "重要商务条款")
_VETO_MARKS = _MANDATORY_MARKS  # 保留兼容性
_VETO_CAP = 80
_HEADING_RE = re.compile(
    r"^(?:第\s*[一二三四五六七八九十百零〇0-9]{1,4}\s*[章部分节]|[一二三四五六七八九十]{1,3}、)"
)
_PAGE_RE = re.compile(r"第\s*(\d{1,4})\s*页")
_FORMAT_MARKS = ("装订", "密封", "正本", "副本", "份数")
_DEV_EMPTY = {"", "待确认", "待补充", "待补", "待响应", "/", "无", "—", "-", "／"}


def _has_veto(line: str) -> bool:
    """识别否决性（mandatory）条款标记。"""
    if any(mark in line for mark in _MANDATORY_MARKS):
        return True
    # 「不得分」是评分用语，不是否决。
    rest = line.replace("不得分", "")
    return "不得" in rest


def _has_important(line: str) -> bool:
    """识别重要性（important）条款标记。"""
    return any(mark in line for mark in _IMPORTANT_MARKS)


def _requirement_strength(line: str) -> str:
    """判断条款强度：mandatory（必须满足否则废标）、important（负偏离扣分）、general。"""
    if _has_veto(line):
        return "mandatory"
    if _has_important(line):
        return "important"
    return "general"


_TECH_VETO_TEXT = (
    "技术标准",
    "技术规格",
    "技术参数",
    "技术指标",
    "技术要求",
    "技术偏离",
    "技术偏差",
    "负偏离",
    "施工组织",
    "施工方案",
    "实施方案",
    "技术方案",
    "暗标",
)
_BIZ_VETO_TEXT = (
    "营业执照",
    "资质",
    "财务报表",
    "信誉",
    "法定代表人",
    "保证金",
    "投标报价",
    "类似项目",
    "类似业绩",
    "授权委托",
    "承诺函",
    "商务偏离",
    "商务偏差",
    "投标函",
    "联合体",
    "项目经理",
    "单位负责人",
    "失信",
    "信用信息",
    "信用",
)
_TECH_REQ_KINDS = frozenset({"tech_plan", "tech_chapter", "tech_dev"})
_BIZ_REQ_KINDS = frozenset(
    {
        "performance",
        "biz_dev",
        "letter",
        "legal_id",
        "auth",
        "quote",
        "commitment_copy",
        "company",
        "factory",
        "scan",
    }
)


def _text_volume(text: str) -> str:
    """句子自己能判断卷别时，不跟章节走。投标人须知里常夹着技术标准。

    资格词压过「质量 / 安全 / 进度」这类宽词（宽词不在这张表里）。
    技术词和资格词同时出现时不在这里拍板。
    """
    n = compact_line(text)
    tech = any(k in n for k in _TECH_VETO_TEXT)
    biz = any(k in n for k in _BIZ_VETO_TEXT)
    if tech and not biz:
        return "technical"
    if biz and not tech:
        return "business"
    return ""


def _chapter_volume(chapter: str) -> str:
    ch = compact_line(chapter)
    if any(k in ch for k in ("技术标", "技术要求", "技术需求", "技术规格", "技术标准", "技术部分", "技术方案")):
        return "technical"
    if any(k in ch for k in ("商务标", "商务部分", "商务条款", "资格", "投标人须知", "供应商须知")):
        return "business"
    return ""


def _veto_kind(text: str, chapter: str = "") -> str:
    """业绩、商务偏离归商务标；技术偏离归技术标。两张偏离表不混用。"""
    from api.services.tenders.match import _kind_hits

    n = compact_line(text)
    ch = compact_line(chapter)
    if "商务偏离" in n or "商务偏差" in n:
        return "biz_dev"
    if any(k in n for k in ("技术偏离", "技术偏差", "技术指标", "技术参数", "技术规格", "负偏离")):
        return "tech_dev"
    if "perf" in _kind_hits(text) or "业绩" in n or "类似项目" in n:
        return "performance"
    if "偏离" in n or "偏差" in n:
        if "商务" in ch or "商务" in n:
            return "biz_dev"
        return "tech_dev"
    if "暗标" in n or any(mark in n for mark in _FORMAT_MARKS):
        return "format"
    return ""


def veto_volume(text: str, chapter: str = "", *, target_kind: str = "", slot_key: str = "") -> str:
    """优先级：传入的 tech_plan/tech_chapter/tech_dev，再商务 kind，再推断 kind，
    再槽位，再句子（资格词压过质量/安全/进度；两边都有则不在句子层拍板），再章节，最后商务。
    """
    from api.services.tenders.categories import slot_volume
    from api.services.tenders.match import _kind_hits

    given = (target_kind or "").strip()
    if given in _TECH_REQ_KINDS:
        return "technical"
    if given in _BIZ_REQ_KINDS:
        return "business"
    kind = _veto_kind(text, chapter)
    if kind in {"performance", "biz_dev"}:
        return "business"
    if kind == "tech_dev":
        return "technical"
    if kind == "format":
        return "technical" if "暗标" in compact_line(text) else "business"
    if slot_key:
        return slot_volume(slot_key, "")
    hits = _kind_hits(text)
    if "product" in hits:
        return "technical"
    if hits:
        key = "perf" if "perf" in hits else next(iter(hits))
        if key == "perf":
            return "business"
        return slot_volume(key, "")
    text_vol = _text_volume(text)
    if text_vol:
        return text_vol
    ch_vol = _chapter_volume(chapter)
    if ch_vol:
        return ch_vol
    n = compact_line(text)
    if any(k in n for k in ("技术方案", "技术要求", "图纸", "参数")):
        return "technical"
    return "business"


def extract_veto_requirements(text: str) -> list[BidRequirement]:
    """整本扫否决句。不走 classify 的噪声表：那里把「废标」从目录里丢掉了。"""
    chapter = ""
    page = ""
    out: list[BidRequirement] = []
    seen: set[str] = set()
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        page_hit = _PAGE_RE.search(line)
        if page_hit and len(compact_line(line)) <= 10:
            page = page_hit.group(1)
            continue
        if page_hit:
            page = page_hit.group(1)
        if _HEADING_RE.match(line) and len(line) <= 40:
            chapter = re.sub(r"\s+", "", line)[:40]
            continue
        if not _has_veto(line):
            continue
        if len(line) < 8 or len(line) > 400:
            continue
        if "http" in line.lower() or ".html" in line.lower():
            continue
        body = re.sub(r"\s+", "", line)[:200]
        key = body[:80]
        if key in seen:
            continue
        seen.add(key)
        kind = _veto_kind(body, chapter)
        out.append(
            BidRequirement(
                id=f"v{len(out) + 1:02d}",
                text=body,
                category="veto",
                targetKind=kind,
                strength="mandatory",
                sourceChapter=chapter,
                sourcePage=page,
                volume=veto_volume(body, chapter, target_kind=kind),
            )
        )
        if len(out) >= _VETO_CAP:
            break
    return out


def _req_key(text: str) -> str:
    return re.sub(r"[\s★※]+", "", text or "")[:80]


def merge_veto_requirements(
    base: list[BidRequirement],
    veto: list[BidRequirement],
) -> list[BidRequirement]:
    """原有评分/资格分类留着。同一句只升级强度，不另起一条。"""
    out = list(base or [])
    index = {_req_key(item.text): i for i, item in enumerate(out) if _req_key(item.text)}
    for item in veto or []:
        key = _req_key(item.text)
        if not key:
            continue
        hit = index.get(key)
        if hit is None:
            for i, prev in enumerate(out):
                prev_key = _req_key(prev.text)
                if not prev_key or min(len(key), len(prev_key)) < 8:
                    continue
                if key in prev_key or prev_key in key:
                    hit = i
                    break
        if hit is not None:
            prev = out[hit]
            out[hit] = prev.model_copy(
                update={
                    "strength": "mandatory",
                    "sourceChapter": prev.sourceChapter or item.sourceChapter,
                    "sourcePage": prev.sourcePage or item.sourcePage,
                    "targetKind": prev.targetKind or item.targetKind,
                    "volume": item.volume or prev.volume,
                }
            )
            continue
        nxt = item.model_copy(update={"id": f"v{len(out) + 1:02d}"})
        index[key] = len(out)
        out.append(nxt)
    return out


def _table_kinds(outline_items: list, layout_mode: str) -> set[str]:
    kinds = {
        (getattr(item, "kind", "") or "").strip()
        for item in outline_items or []
        if not getattr(item, "skipped", False)
    }
    kinds.discard("")
    if (layout_mode or "").strip() != "outline":
        kinds.update({"performance", "biz_dev", "tech_dev"})
    return kinds


def _perf_selected(lines) -> int:
    n = 0
    for row in lines or []:
        name = (getattr(row, "projectName", "") or "").strip()
        if name and getattr(row, "includeInBid", True):
            n += 1
    return n


def _deviation_filled(lines) -> bool:
    for row in lines or []:
        req = (getattr(row, "requirement", "") or "").strip()
        resp = (getattr(row, "response", "") or "").strip()
        if req and req not in _DEV_EMPTY:
            return True
        if resp and resp not in _DEV_EMPTY:
            return True
    return False


def biz_clause_filled(brief: BidBrief | None) -> bool:
    if brief is None:
        return False
    if brief.deliveryDays or brief.warrantyYears or brief.bidValidityDays:
        return True
    if (brief.quality or "").strip():
        return True
    return any(
        getattr(brief, key, 0)
        for key in ("prepaidPct", "arrivalPct", "settlementPct", "warrantyPct")
    )


def _bucket_of(item: BidRequirement) -> str:
    kind = (item.targetKind or "").strip()
    if kind in {"performance", "tech_dev", "biz_dev", "format"}:
        return kind
    if kind == "deviation":
        return "biz_dev" if "商务" in (item.text or "") else "tech_dev"
    return _veto_kind(item.text, item.sourceChapter)


def _decorate_requirement(item: BidRequirement) -> BidRequirement:
    text = item.text or ""
    kind = (item.targetKind or "").strip()
    cat = (item.category or "").strip()
    if kind == "format" or cat == "format":
        respond, display = "声明", ""
    elif kind == "tech_dev" or cat == "scoring":
        respond, display = "偏离表", "偏离表"
    elif kind == "biz_dev" or any(k in text for k in ("工期", "质保", "付款", "保证金", "交货")):
        respond, display = "承诺函", "承诺"
    elif kind == "performance" or cat in {"qualification", "material", "performance", "veto"}:
        respond, display = "证明材料", "落位"
    else:
        respond, display = "声明", "对照表"
    section = (item.attachToSection or item.targetTitle or "").strip()
    return item.model_copy(
        update={
            "reqType": item.reqType or cat or kind or "general",
            "respond": item.respond or respond,
            "display": item.display or display,
            "attachToSection": section,
        }
    )


def bind_requirements(
    reqs: list[BidRequirement],
    *,
    catalog: list | None = None,
    file_counts: dict[str, int] | None = None,
    outline_items: list | None = None,
    performance_lines: list | None = None,
    deviation_lines: list | None = None,
    layout_mode: str = "",
    biz_filled: bool = False,
) -> list[BidRequirement]:
    """只给否决项标证据。技术偏离只看技术偏离表，商务偏离只看商务偏离表。"""
    from api.services.tenders.match import _kind_hits, find_catalog_item
    from api.services.tenders.schema import PlaceholderItem

    counts = file_counts or {}
    slots = [item for item in (catalog or []) if isinstance(item, PlaceholderItem) and item.key]
    places = _table_kinds(outline_items, layout_mode)
    selected = _perf_selected(performance_lines)
    dev_ok = _deviation_filled(deviation_lines)
    out: list[BidRequirement] = []
    for item in reqs or []:
        if (item.strength or "") != "mandatory":
            out.append(_decorate_requirement(item))
            continue
        bucket = _bucket_of(item)
        status = "unbound"
        note = "格式章无对应空位，只在清单里提示，不新开章节"
        slot_key = ""
        if bucket == "format":
            status = "format"
            note = "装订、密封、份数归商务标；暗标归技术标。只提示，不按材料计算覆盖"
        elif bucket == "performance":
            if "performance" not in places:
                status = "unbound"
                note = (
                    f"已勾选 {selected} 条业绩，商务标格式章没有业绩表，生成不会写入"
                    if selected
                    else "商务标格式章没有业绩表，生成不会写入"
                )
            elif selected > 0:
                status = "matched"
                note = f"商务标已勾选 {selected} 条业绩"
            else:
                status = "gap"
                note = "商务标有业绩表，尚未勾选业绩"
        elif bucket == "tech_dev":
            if "tech_dev" not in places:
                status = "unbound"
                note = (
                    "技术偏离已填写，技术标格式章没有技术偏离表，生成不会写入"
                    if dev_ok
                    else "技术标格式章没有技术偏离表，生成不会写入"
                )
            elif dev_ok:
                status = "matched"
                note = "技术偏离表已有填写"
            else:
                status = "gap"
                note = "技术标有技术偏离表，尚未填写"
        elif bucket == "biz_dev":
            if "biz_dev" not in places:
                status = "unbound"
                note = "商务标格式章没有商务偏离表，生成不会写入。不拿技术偏离表顶。"
            elif biz_filled:
                status = "matched"
                note = "商务偏离按交货期、质保、付款写入，不看技术偏离表"
            else:
                status = "gap"
                note = "商务标有商务偏离表，交货期、质保、付款还没填"
        elif not bucket and _kind_hits(item.text):
            found = find_catalog_item(slots, title=item.text)
            if found is not None and found.key:
                slot_key = found.key
                nfile = int(counts.get(found.key) or 0)
                if nfile > 0:
                    status = "matched"
                    note = f"资料库已有扫描件 {nfile} 份"
                else:
                    status = "gap"
                    note = f"对应「{found.title or found.key}」，资料库还没有文件"
            else:
                status = "gap"
                note = "对得上资料种类，资料库还没有这项"
        n = "".join((item.text or "").split())
        if (
            status == "unbound"
            and note.startswith("格式章无对应空位")
            and any(m in n for m in ("资质证明", "资质证书", "企业资质", "资质文件"))
        ):
            note = "未匹配到资料，生成时在「其他资料」里占位"
        slot_key = slot_key or item.slotKey
        vol = veto_volume(
            item.text,
            item.sourceChapter,
            target_kind=bucket,
            slot_key=slot_key,
        )
        out.append(
            _decorate_requirement(
                item.model_copy(
                    update={
                        "matchStatus": status,
                        "matchNote": note,
                        "slotKey": slot_key,
                        "targetKind": bucket or item.targetKind,
                        "volume": vol,
                    }
                )
            )
        )
    return out


def evidence_coverage(
    brief: BidBrief,
    *,
    catalog: list | None = None,
    file_counts: dict[str, int] | None = None,
    volume: str = "",
) -> dict:
    """否决项覆盖。红线固定关着，调用方不得拿这个结果拦下载。"""
    bound = bind_requirements(
        list(brief.bidRequirements or []) if brief is not None else [],
        catalog=catalog,
        file_counts=file_counts,
        outline_items=list(brief.outlineItems or []) if brief is not None else [],
        performance_lines=list(brief.performanceLines or []) if brief is not None else [],
        deviation_lines=list(brief.deviationLines or []) if brief is not None else [],
        layout_mode=(brief.layoutMode or "") if brief is not None else "",
        biz_filled=biz_clause_filled(brief),
    )
    vol = (volume or "").strip().lower()
    rows: list[dict] = []
    for item in bound:
        if (item.strength or "") != "mandatory":
            continue
        row_vol = (item.volume or "").strip()
        if row_vol not in {"business", "technical"}:
            continue
        if vol in {"business", "technical"} and row_vol != vol:
            continue
        rows.append(
            {
                "id": item.id,
                "text": item.text,
                "volume": row_vol,
                "matchStatus": item.matchStatus,
                "matchNote": item.matchNote,
                "sourceChapter": item.sourceChapter,
                "sourcePage": item.sourcePage,
                "slotKey": item.slotKey,
                "respond": item.respond,
                "attachToSection": item.attachToSection,
            }
        )
    counted = [row for row in rows if row["matchStatus"] != "format"]
    covered = sum(1 for row in counted if row["matchStatus"] == "matched")
    total = len(counted)
    rate = round(100 * covered / total) if total else 100
    return {
        "redLine": False,
        "mandatoryCovered": covered,
        "mandatoryTotal": total,
        "coverageRate": rate,
        "gapCount": total - covered,
        "formatCount": sum(1 for row in rows if row["matchStatus"] == "format"),
        "rows": rows,
    }


def extract_requirements(text: str) -> list[BidRequirement]:
    out: list[BidRequirement] = []
    seen: set[str] = set()
    for raw in (text or "").splitlines():
        for piece in raw.split("|"):
            s = piece.strip()
            if len(s) < 8:
                continue
            hit = classify_content(s)
            if hit.role != "requirement":
                continue
            key = compact_line(strip_item_prefix(s) or s)[:80]
            if len(key) < 8 or key in seen:
                continue
            seen.add(key)
            title = strip_item_prefix(s) or s
            title = re.sub(r"\s+", "", title)[:120]
            # 三级条款强度识别
            strength = _requirement_strength(s)
            out.append(
                BidRequirement(
                    id=f"r{len(out) + 1:02d}",
                    text=title,
                    category=hit.category or "notice",
                    targetKind=hit.target_kind,
                    targetTitle=hit.target_title,
                    strength=strength,
                )
            )
            if len(out) >= 40:
                return out
    return out


def apply_bid_requirements(brief: BidBrief, reqs: list[BidRequirement]) -> list[str]:
    brief.bidRequirements = list(reqs or [])
    if not reqs:
        return []
    notes: list[str] = []
    perf = [r.text for r in reqs if r.category == "performance"]
    if perf:
        _merge_perf(brief, perf)
    n_qual = sum(1 for r in reqs if r.category in {"qualification", "material"})
    n_veto = sum(1 for r in reqs if (r.strength or "") == "mandatory")
    n_important = sum(1 for r in reqs if (r.strength or "") == "important")
    n_biz = sum(1 for r in reqs if (r.strength or "") == "mandatory" and r.volume == "business")
    n_tech = sum(1 for r in reqs if (r.strength or "") == "mandatory" and r.volume == "technical")
    notes.append(
        f"招标要求 {len(reqs)} 条已从组卷目录分开"
        + (f"，其中资格/佐证 {n_qual} 条" if n_qual else "")
        + "，生成时按对应章节响应，不写入投标书目录"
    )
    if n_veto:
        notes.append(
            f"必须响应项（★）{n_veto} 条已按卷分开：商务标 {n_biz} 条，技术标 {n_tech} 条。"
            "完全响应，负偏离=废标。"
        )
    if n_important:
        notes.append(
            f"重要响应项（▲）{n_important} 条：尽量响应，负偏离扣分不废标。"
        )
    if n_veto or n_important:
        notes.append(
            "切换左上角只看当前卷。可删除误报；重新识别会整份替换。不拦生成和下载。"
        )
    return notes


def requirement_group_label(kind: str, category: str = "") -> str:
    if kind in _KIND_LABELS:
        return _KIND_LABELS[kind]
    if category == "qualification":
        return "资格"
    if category == "material":
        return "佐证材料"
    if category == "scoring":
        return "技术标"
    return "其他"


def _is_invite_blank(raw: str) -> bool:
    s = (raw or "").strip()
    if re.search(r"[：:]\s*[/／]\s*$", s):
        return True
    core = re.sub(r"[\s]+", "", s)
    return bool(re.search(r"[：:][/／]+$", core))


def _is_noise(n: str, raw: str) -> bool:
    if _is_invite_blank(raw):
        return True
    if re.search(r"\.html", n, re.I):
        return True
    if any(k in n for k in _NOISE_MARKS):
        return True
    if n.startswith(("无", "未", "不得", "禁止", "未加盖", "未提供")) and any(
        k in n for k in ("印鉴", "公章", "签字", "盖章")
    ):
        return True
    if "无单位" in n and any(k in n for k in ("印鉴", "公章")):
        return True
    if any(k in n for k in ("废标", "否决投标", "按无效", "作无效")):
        return True
    if "报价表说明" in n or "人民币元" in n:
        return True
    if _is_eval_table(n):
        return True
    if "技术标准" in n and "要求" in n:
        return True
    return False


def _is_eval_table(n: str) -> bool:
    if "见下表" in n or "如下表" in n:
        return True
    if "基准价" in n:
        return True
    if any(k in n for k in _EVAL_TABLE_MARKS):
        return True
    if "评审" in n and any(k in n for k in ("分", "计分", "评分")):
        return True
    if "计算规则" in n and "报价表" not in n:
        return True
    return False


def _is_scoring(n: str) -> bool:
    if any(k in n for k in _SCORE_MARKS):
        return True
    if "实施方案" in n and any(k in n for k in ("符合", "针对性", "评分")):
        return True
    return False


def _is_material(n: str) -> bool:
    return any(k in n for k in _MATERIAL_MARKS)


def _is_qual(n: str) -> bool:
    if any(k in n for k in _QUAL_MARKS):
        return True
    if "吊销" in n and any(k in n for k in ("执照", "资质")):
        return True
    if "责令停业" in n:
        return True
    return False


def _is_perf(n: str) -> bool:
    if "是指" in n and any(k in n for k in ("类似项目", "类似业绩", "同类项目")):
        return True
    return any(k in n for k in _PERF_MARKS)


def _is_notice_req(n: str) -> bool:
    return any(k in n for k in _NOTICE_MARKS)


def _looks_like_chapter(n: str) -> bool:
    if n.endswith(("见下表", "如下表")):
        return False
    if any(k in n for k in _STD_MARKS) and len(n) <= 24:
        return True
    if n.endswith(_CHAPTER_ENDS) and len(n) <= 24:
        return True
    if len(n) <= 16 and any(
        k in n
        for k in (
            "营业执照",
            "资质证书",
            "资质证明",
            "企业资质",
            "资质文件",
            "资格证明",
            "财务报表",
            "财务审计",
            "财务报告",
            "审计报告",
            "扫描件",
            "身份证明",
            "授权委托",
        )
    ):
        return True
    return 3 <= len(n) <= 12


def _material_kind(n: str) -> str:
    if any(k in n for k in ("业绩", "类似项目", "情况表")):
        return "performance"
    if any(k in n for k in ("财务", "审计")):
        return "scan"
    if any(k in n for k in ("执照", "资质")):
        return "scan"
    return "scan"


def _quoted_table(n: str) -> str:
    m = re.search(r"[“\"「]([^”\"」]{4,24})[”\"」]", n)
    if m:
        return m.group(1)
    return ""


def _merge_perf(brief: BidBrief, texts: list[str]) -> None:
    req = brief.performanceRequirement or PerformanceRequirement()
    note = (req.note or "").strip()
    extra = texts[0][:160]
    if extra and extra not in note:
        note = f"{note} {extra}".strip() if note else extra
    scope = (req.similarScope or "").strip() or _scope_from_defs(texts)
    keywords = list(req.keywords or [])
    for word in ("充电站", "充电桩", "供配电"):
        blob = "".join(texts)
        if word in blob and word not in keywords:
            keywords.append(word)
    brief.performanceRequirement = PerformanceRequirement(
        similarScope=(scope or req.similarScope or "")[:80],
        keywords=keywords[:8],
        minAmountYuan=req.minAmountYuan or 0,
        minCount=req.minCount or 0,
        requireCompleted=bool(req.requireCompleted),
        note=note[:160],
    )


_SPLIT_MARKS = (
    "分别装订",
    "分册装订",
    "分开装订",
    "单独装订",
    "各一册",
    "商务标和技术标",
    "商务标、技术标",
    "技术标和商务标",
    "分为商务标",
    "分为商务部分",
    "商务文件和技术文件",
    "技术文件和商务文件",
    "暗标单独成册",
)
_ANON_MARKS = (
    "技术暗标",
    "暗标评审",
    "暗标部分",
    "暗标编制",
    "暗标装订",
    "暗标单独",
    "（暗标",
    "(暗标",
    "不得出现投标人名称",
    "不得出现投标单位名称",
    "不得出现单位名称",
)
# 不得出现……徽标、人员名称。中间可以插几个字，负偏离这类句子对不上。
_ANON_NEAR = re.compile(
    r"不得出现[^。]{0,48}(?:投标人名称|投标单位|单位名称|徽标|标识|人员名称|人员姓名)"
)
# 附表七常是施工组织暗标的编制及装订要求，标题里不一定再写「暗标」。
_ANON_APPENDIX = re.compile(r"附表七[^。]{0,48}编制及装订要求")
ANON_DEFER_NOTE = "暗标封面封底版式（不署名、合并装订成一册的装订要求）留待暗标专轮。"


def _tech_anonymous(text: str) -> bool:
    """招标原文里的暗标强信号。宁可判暗标，漏判会把名字写进技术卷。"""
    n = re.sub(r"\s+", "", text or "")
    if not n:
        return False
    if any(mark in n for mark in _ANON_MARKS):
        return True
    if _ANON_NEAR.search(n):
        return True
    return _ANON_APPENDIX.search(n) is not None


def judge_binding(text: str) -> tuple[str, bool, str]:
    """扫编制/装订条款。不调模型。四册只写进说明，本轮仍是一份或商务+技术两份。"""
    n = re.sub(r"\s+", "", text or "")
    anon = _tech_anonymous(n)
    four = ("第一册" in n and "第四册" in n) or ("四册" in n and "装订" in n)
    split = four or any(mark in n for mark in _SPLIT_MARKS)
    if not split and "商务标" in n and "技术标" in n and any(k in n for k in ("装订", "分册", "分别")):
        split = True
    if four:
        advice = "split"
        note = "招标文件写了多册装订，本轮仍只出一份或商务标、技术标两份，不另做四册。"
    elif split:
        advice = "split"
        note = "招标文件要求商务标与技术标分册装订。"
    else:
        advice = "full"
        note = "招标文件未要求分册装订，按连续目录出完整投标文件。"
    if anon:
        note = f"{note}技术方案按技术暗标处理。{ANON_DEFER_NOTE}"
    return advice, anon, note


def apply_volume_binding(brief: BidBrief, text: str, *, keep_volume: bool) -> BidBrief:
    advice, anon, note = judge_binding(text)
    update: dict[str, object] = {
        "volumeAdvice": advice,
        "techAnonymous": anon,
        "volumeNote": note,
    }
    cur = (brief.generateVolume or "").strip()
    if not keep_volume and advice == "full" and cur in {"", "business"}:
        update["generateVolume"] = "full"
    return brief.model_copy(update=update)


def _scope_from_defs(texts: list[str]) -> str:
    for t in texts:
        n = compact_line(t)
        m = re.search(r"是指[：:]*([^。；;]{4,48})", n)
        if not m:
            continue
        s = re.split(r"招标人|不得超过|业绩要求", m.group(1))[0]
        s = s.strip("，,。；; ")
        if 4 <= len(s) <= 40:
            return s
    return ""
