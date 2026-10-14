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
            out.append(
                BidRequirement(
                    id=f"r{len(out) + 1:02d}",
                    text=title,
                    category=hit.category or "notice",
                    targetKind=hit.target_kind,
                    targetTitle=hit.target_title,
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
    notes.append(
        f"招标要求 {len(reqs)} 条已从组卷目录分开"
        + (f"，其中资格/佐证 {n_qual} 条" if n_qual else "")
        + "，生成时按对应章节响应，不写入投标书目录"
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
    if re.match(r"^[A-Ha-h][.．、]", (raw or "").strip()) and any(
        k in n for k in ("营业执照", "业绩", "财务", "其它文件", "其他文件")
    ):
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
