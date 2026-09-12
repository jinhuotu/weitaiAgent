"""从招标/邀请书正文抽出「投标/响应文件格式」组卷大纲。规则优先，不写承诺函正文。"""

from __future__ import annotations

import re

from api.services.tenders.schema import OUTLINE_KINDS, OutlineItem

_FORMAT_HEAD = re.compile(
    r"(?P<title>(?:第[一二三四五六七八九十0-9]+[章节部分][^\n]{0,24})?"
    r"(?:投标文件格式|响应文件格式|投标文件组成|响应文件组成|"
    r"投标文件的组成|响应文件的组成))"
)

_CHAPTER_LINE = re.compile(
    r"^第(?P<num>[一二三四五六七八九十0-9]+)(?P<unit>[章节部分])\s*(?P<rest>\S.*)?$"
)

_ITEM_PREFIX = re.compile(
    r"^(?:"
    r"附件[（(]?[一二三四五六七八九十0-9]+[)）]?"
    r"|[（(][一二三四五六七八九十0-9]+[)）]"
    r"|[一二三四五六七八九十]{1,2}、"
    r"|[0-9]{1,2}[.．、]"
    r")[、.．:：\s]*"
)

_ATTACH_HINT = re.compile(r"附件[（(]?[一二三四五六七八九十0-9]+")
_ATTACH_SEQ = re.compile(r"附件[（(]?([一二三四五六七八九十0-9]+)[)）]?")

_PAGE_TAIL = re.compile(r"[\s.·•…]*[0-9]{1,4}$")

_SKIP_SUB = re.compile(
    r"(递交|时间|地点|页码|密封|份数|正本|副本|电子版|电子标书|扫描版|可编辑|"
    r"同步提供|盖章|签字|我方承诺|我方在此|应当逐条|如响应文件|响应供应商根据)"
)

_FIELD_KV = re.compile(r"[：:]")
_CLAUSE_START = re.compile(r"^(具有|具备|应当|必须|不得|需具备)")
_CONTACT = re.compile(r"(监督人|联系人|联系电话|联系方式|手机号|传真|经办人)")
_PHONE = re.compile(r"\d{7,}")
_FIELD_LABEL = re.compile(
    r"^(招标编号|项目编号|采购编号|招标单位|采购人|项目名称|投标人名称|"
    r"供应商名称|日期|地址|授权期间|授权期限|委托期限|单位名称)$"
)
_NOT_DOC_HEAD = re.compile(r"(资格要求|资格条件|符合性要求|评分办法|评标办法)$")

# 目录标题才有的材料名；不用单字「书/证明/承诺/报价」，避免正文句子误中。
_DOC_NOUN = re.compile(
    r"(?:函|清单|资料|保函|执照|证书|备案|保证金|竞标书|竞标|"
    r"承诺书|承诺函|授权书|委托书|报价表|报价单|报价清单|"
    r"身份证明|资质证明|业绩证明|资格证明|偏离|工具表|"
    r"表(?:[（(]|$))"
)

# 表格填空、装订说明、落款，不是目录行。
_FORM_DEBRIS = re.compile(
    r"(粘贴处|公章|（章）|\(章\)|单位名称|投标人名称|"
    r"授权期间|授权期限|年月日|"
    r"一式[一二三四五六七八九十0-9]+份|一正.?副|纸质版)"
)

# 函件/证明正文，不是章节名。
_SENTENCE = re.compile(
    r"(我公司|我方|现做出|如下承诺|^特此|特此证明|"
    r"^系[（(]|的法定代表人|保证投标文件|不存在低于|恶意报价|"
    r"的报价不存在)"
)

_ORG_TAIL = re.compile(r"(有限公司|股份有限公司|集团有限公司|集团公司)$")
_FORMAT_IN_TITLE = re.compile(r"(投标文件格式|响应文件格式|投标文件组成|响应文件组成)")

_GENERATE_KINDS = frozenset(
    {"letter", "legal_id", "auth", "quote", "biz_dev", "tech_dev", "performance", "factory", "tech_plan"}
)


def compact_title(text: str) -> str:
    return re.sub(r"[\s/／|｜]+", "", text or "")


def _outline_key(title: str) -> str:
    n = compact_title(title)
    n = _ATTACH_HINT.sub("", n)
    n = re.sub(r"^附件", "", n)
    n = re.sub(r"[（）()：:、.．]", "", n)
    return n or compact_title(title)


def classify_kind(title: str) -> str:
    n = compact_title(title)
    if not n:
        return "unknown"
    if "商务" in n and "偏离" in n:
        return "biz_dev"
    if "技术" in n and "偏离" in n:
        return "tech_dev"
    if "偏离表" in n or n.endswith("偏离"):
        return "tech_dev"
    if any(k in n for k in ("身份证明", "身份证复印件", "法定代表人身份", "负责人身份")):
        return "legal_id"
    if "授权" in n and ("委托" in n or n.endswith("授权书")):
        return "auth"
    if any(k in n for k in ("报价清单", "报价表", "报价单", "分项报价", "工程量清单")):
        return "quote"
    if any(k in n for k in ("投标函附录", "响应函附录")):
        return "letter"
    if any(k in n for k in ("投标函", "响应函")):
        return "letter"
    if any(k in n for k in ("承诺函", "承诺书")):
        return "commitment_copy"
    if any(k in n for k in ("类似业绩", "企业业绩", "合同业绩", "类似项目", "业绩证明")):
        return "performance"
    if "原厂" in n:
        return "factory"
    if any(k in n for k in ("实施方案", "技术标")):
        return "tech_plan"
    if any(k in n for k in ("营业执照", "资质证书", "资质证明", "扫描件", "资格证明", "保证金", "保函")):
        return "scan"
    if any(k in n for k in ("税务信息", "关联关系", "企业基本情况", "供应商基本情况", "印鉴", "备案表")):
        return "company"
    return "unknown"


def source_for_kind(kind: str, *, skipped: bool = False) -> str:
    if skipped:
        return "skip"
    if kind in _GENERATE_KINDS:
        return "generate"
    return "copy"


def is_noise_title(title: str) -> bool:
    """空白模板里的页眉、填空、正文句子，不是组卷目录条目。"""
    n = compact_title(title)
    if not n:
        return True
    if _FIELD_KV.search(title):
        return True
    if n.endswith("；") or n.endswith(";"):
        return True
    if _CLAUSE_START.search(n):
        return True
    if _CONTACT.search(n) or _PHONE.search(n):
        return True
    if _FIELD_LABEL.match(n):
        return True
    if _NOT_DOC_HEAD.search(n):
        return True
    if _SKIP_SUB.search(n):
        return True
    if _FORM_DEBRIS.search(n):
        return True
    if _SENTENCE.search(n):
        return True
    if _is_running_header(n):
        return True
    if _is_mashed_form_title(n):
        return True
    return False


def extract_outline(text: str) -> tuple[str, list[OutlineItem]]:
    """从邀请书/招标书正文抽出格式章节条目。找不到则返回空列表，不报错。"""
    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    chapter, body = _format_section(raw)
    lines = _candidate_lines(body or raw, whole_doc=not bool(body))
    items: list[OutlineItem] = []
    seen: set[str] = set()
    for title in lines:
        key = _outline_key(title)
        if key in seen or len(key) < 2:
            continue
        seen.add(key)
        kind = classify_kind(title)
        kind = kind if kind in OUTLINE_KINDS else "unknown"
        if kind == "unknown":
            continue
        items.append(
            OutlineItem(
                id=f"o{len(items) + 1:02d}",
                title=title,
                kind=kind,
                source=source_for_kind(kind),
                required=True,
                skipped=False,
            )
        )
        if len(items) >= 40:
            break
    _attach_item_bodies(items, body)
    return chapter, items


_BODY_MAX = 6000


def choose_layout_mode(chapter: str, items: list[OutlineItem]) -> str:
    """有格式章且不像充电桩固定五件套时，默认用本标识别出的大纲。"""
    active = [item for item in items if not item.skipped]
    if not active:
        return "chapter5"
    if "响应文件" in compact_title(chapter):
        return "outline"
    copy_kinds = {"commitment_copy", "company", "biz_dev"}
    copy_n = sum(1 for item in active if item.kind in copy_kinds)
    if copy_n >= 2:
        return "outline"
    if any(item.kind == "biz_dev" for item in active) and any(item.kind == "tech_dev" for item in active):
        return "outline"
    if len(active) >= 6:
        return "outline"
    return "chapter5"


def looks_like_form_template(text: str) -> bool:
    """空白稿：有附件标题/下划线填空/落款。PDF 常抽成一两行，不能只按行数判断。"""
    raw = (text or "").strip()
    if len(raw) < 60:
        return False
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    markers = 0
    if re.search(r"[＿_]{3,}|_{3,}|[—－]{3,}", raw):
        markers += 1
    if _ATTACH_HINT.search(raw):
        markers += 1
    if re.search(r"(投标人|供应商).{0,8}(章|公章|签字)", raw):
        markers += 1
    if re.search(r"年\s*月\s*日", raw):
        markers += 1
    if re.search(r"致[：:]|有限公司[：:]", raw):
        markers += 1
    if re.search(r"（招标人名称）|（采购人名称）|（项目名称）", raw):
        markers += 1
    if markers < 2:
        return False
    if len(lines) >= 4:
        return True
    return len(raw) >= 80 and markers >= 3


def fill_copy_blanks(
    text: str,
    *,
    bidder: str,
    project: str,
    tenderer: str,
    legal: str,
    bid_date: str,
    phone: str = "",
    address: str = "",
    contact: str = "",
    delivery_days: int | None = None,
    validity_days: int | None = None,
    quality: str = "",
) -> str:
    """只填单位/项目/日期/联系空位，不改承诺条款。"""
    out = text or ""
    bidder = (bidder or "").strip()
    project = (project or "").strip()
    tenderer = (tenderer or "").strip()
    legal = (legal or "").strip()
    phone = (phone or "").strip()
    address = (address or "").strip()
    contact = (contact or "").strip()
    quality = (quality or "").strip()
    date_cn = _date_cn(bid_date)
    pairs = (
        ("（投标人名称）", bidder),
        ("（供应商名称）", bidder),
        ("（响应供应商名称）", bidder),
        ("（响应人名称）", bidder),
        ("（项目名称）", project),
        ("（招标人名称）", tenderer),
        ("（采购人名称）", tenderer),
        ("（法定代表人姓名）", legal),
    )
    for needle, value in pairs:
        if value:
            out = out.replace(needle, value)
    if tenderer:
        out = re.sub(r"致[：:]\s*[＿_—\-]{2,}", f"致：{tenderer}", out)
        out = re.sub(
            r"^[＿_—\-\s]{4,}(?:有限公司|股份有限公司)?[：:]",
            f"{tenderer}：",
            out,
            flags=re.M,
        )
    if project:
        out = re.sub(r"阅读和研究了[＿_—\-]{2,}", f"阅读和研究了{project}", out)
        out = re.sub(r"研究了[＿_—\-]{2,}(?=招标文件|采购文件)", f"研究了{project}", out)
    if bidder:
        out = re.sub(
            r"(投标人名称|供应商名称|响应供应商名称)[：:]\s*[＿_—\-]{2,}",
            rf"\1：{bidder}",
            out,
        )
        out = re.sub(
            r"(投标人|供应商)（章）[：:]\s*[＿_—\-]{2,}",
            rf"\1（章）：{bidder}",
            out,
        )
    # 「签字」栏留给本人手签，不填姓名。
    if contact:
        out = re.sub(r"(联系人)[：:]\s*[＿_—\-\s]{2,}", rf"\1：{contact}", out)
    if phone:
        out = re.sub(r"(联系电话|电\s*话)[：:]\s*[＿_—\-\s]{2,}", rf"\1：{phone}", out)
    if address:
        out = re.sub(r"(地址|住址)[：:]\s*[＿_—\-\s]{2,}", rf"\1：{address}", out)
    if quality:
        out = re.sub(r"(质量要求|质量标准)[：:]\s*[＿_—\-]{2,}", rf"\1：{quality}", out)
    if delivery_days is not None:
        days = str(int(delivery_days))
        out = re.sub(
            r"(通知[^。\n]{0,12}起)[＿_—\-]{1,12}(天)",
            rf"\g<1>{days}\2",
            out,
        )
    if validity_days is not None:
        days = str(int(validity_days))
        out = re.sub(
            r"(开标之日起|投标之日起|截止之日起)[＿_—\-]{1,12}(天)",
            rf"\g<1>{days}\2",
            out,
        )
    if date_cn:
        out = re.sub(r"[＿_—\-]{0,6}年[＿_—\-]{0,4}月[＿_—\-]{0,4}日", date_cn, out)
        out = re.sub(r"年\s*月\s*日", date_cn, out)
    return out


def _date_cn(raw: str) -> str:
    text = (raw or "").strip()
    match = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if match:
        return f"{match.group(1)}年{int(match.group(2)):02d}月{int(match.group(3)):02d}日"
    return text


def _attach_item_bodies(items: list[OutlineItem], chapter_body: str) -> None:
    if not items or not (chapter_body or "").strip():
        return
    starts = _template_starts(chapter_body, items)
    length = len(chapter_body)
    for i, item in enumerate(items):
        start = starts[i]
        if start < 0:
            continue
        later = [pos for pos in starts if pos > start]
        end = min(later) if later else length
        chunk = chapter_body[start:end]
        item.body = _clean_copied_body(chunk, item.title)


def _template_starts(body: str, items: list[OutlineItem]) -> list[int]:
    found: list[list[int]] = [[] for _ in items]
    offset = 0
    for raw in body.splitlines(keepends=True):
        line = raw.strip()
        if line:
            for i, item in enumerate(items):
                if _is_body_heading(line, item.title):
                    found[i].append(offset)
        offset += len(raw)
    starts: list[int] = []
    for hits in found:
        if len(hits) >= 2:
            starts.append(hits[1])
        elif hits:
            starts.append(hits[0])
        else:
            starts.append(-1)
    return starts


def _core_doc_name(text: str) -> str:
    """去掉附件序号、括号后的材料名，便于目录「投标函（附件一）」对上正文「投 标 函」。"""
    n = compact_title(text)
    n = _ATTACH_HINT.sub("", n)
    n = re.sub(r"^附件", "", n)
    n = re.sub(r"[（）()：:、.．]", "", n)
    return n


def _attach_seq(text: str) -> str:
    match = _ATTACH_SEQ.search(compact_title(text) or text or "")
    return match.group(1) if match else ""


def _is_body_heading(line: str, title: str) -> bool:
    n = compact_title(line)
    t = compact_title(title)
    if not t or not n:
        return False
    if len(n) > 36:
        return False
    if _SENTENCE.search(n) or _CLAUSE_START.search(n):
        return False
    rest = _ATTACH_HINT.sub("", n)
    rest = re.sub(r"[（）()]", "", rest)
    if rest == t or n == t:
        return True
    if n.endswith(t) and 0 < len(n) - len(t) <= 8:
        prefix = n[: len(n) - len(t)]
        if prefix in {"附件", "附表"} or _ATTACH_HINT.match(prefix):
            return True
    core_line = _core_doc_name(line)
    core_title = _core_doc_name(title)
    if core_line and core_title:
        if core_line == core_title:
            return True
        if len(core_line) >= 4 and core_title.endswith(core_line):
            return True
    line_seq = _attach_seq(line)
    title_seq = _attach_seq(title)
    if line_seq and title_seq and line_seq == title_seq and not core_line:
        return True
    return False


def _is_skipped_form_prefix(line: str) -> bool:
    raw = (line or "").strip()
    if not raw:
        return True
    if re.fullmatch(r"[-—–_＿]{6,}", raw):
        return True
    n = compact_title(raw)
    return bool(_FORMAT_IN_TITLE.search(n))


def _clean_copied_body(chunk: str, title: str) -> str:
    del title
    text = (chunk or "").strip()
    if not text:
        return ""
    lines = text.splitlines()
    while lines and _is_skipped_form_prefix(lines[0]):
        lines = lines[1:]
    text = "\n".join(lines).strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    if len(text) < 12:
        return ""
    compact = compact_title(text)
    if "\n" not in text and len(compact) <= 28 and classify_kind(text) != "unknown":
        return ""
    return text[:_BODY_MAX]


def _format_section(text: str) -> tuple[str, str]:
    match = _FORMAT_HEAD.search(text)
    if not match:
        return "", ""
    title = re.sub(r"\s+", "", match.group("title") or "").strip()
    start = match.end()
    rest = text[start:]
    stop = len(rest)
    start_num = ""
    head_line = _CHAPTER_LINE.match(title)
    if head_line:
        start_num = head_line.group("num") or ""
    if not start_num:
        return title, rest[:8000]
    for line in rest.splitlines():
        stripped = line.strip()
        ch = _CHAPTER_LINE.match(stripped)
        if not ch:
            continue
        num = ch.group("num") or ""
        rest_title = ch.group("rest") or ""
        if num == start_num:
            continue
        if "格式" in rest_title or "组成" in rest_title:
            continue
        idx = rest.find(line)
        if idx >= 0:
            stop = idx
            break
    return title, rest[:stop]


def _candidate_lines(body: str, *, whole_doc: bool) -> list[str]:
    out: list[str] = []
    for raw in (body or "").splitlines():
        for piece in raw.split("|"):
            title = _clean_item(piece)
            if not title or is_noise_title(title):
                continue
            if not _is_catalog_item(piece, title, whole_doc=whole_doc):
                continue
            out.append(title)
    return out


def _is_catalog_item(raw: str, title: str, *, whole_doc: bool) -> bool:
    """材料名或「材料名（附件N）」才收；公司名+附件N 的页眉不收。"""
    piece = (raw or "").strip()
    n = compact_title(title)
    known = classify_kind(title) != "unknown"
    attach = bool(_ATTACH_HINT.search(piece) or _ATTACH_HINT.search(title))
    prefixed = bool(_ITEM_PREFIX.match(piece))
    doc_like = bool(_DOC_NOUN.search(n))
    if known or (attach and _attach_has_doc_name(n)):
        return True
    if prefixed and doc_like:
        return True
    if whole_doc:
        return False
    return doc_like and 3 <= len(n) <= 28


def _attach_remainder(title: str) -> str:
    n = compact_title(title)
    rest = _ATTACH_HINT.sub("", n)
    rest = re.sub(r"[（）()]", "", rest)
    rest = _FORMAT_IN_TITLE.sub("", rest)
    rest = re.sub(r"第[一二三四五六七八九十0-9]+[章节部分]", "", rest)
    return rest.strip()


def _is_running_header(title: str) -> bool:
    """页眉常把招标人名称和「附件N」粘在一行，中间没有材料名。"""
    n = compact_title(title)
    if not _ATTACH_HINT.search(n):
        return False
    rest = _attach_remainder(n)
    if not rest:
        return True
    if _ORG_TAIL.search(rest) or rest.endswith("公司"):
        return True
    if "有限公司" in rest and classify_kind(rest) == "unknown" and not _DOC_NOUN.search(rest):
        return True
    return False


def _attach_has_doc_name(title: str) -> bool:
    rest = _attach_remainder(title)
    if not rest:
        return False
    if _is_running_header(title):
        return False
    return classify_kind(rest) != "unknown" or bool(_DOC_NOUN.search(rest))


def _is_mashed_form_title(title: str) -> bool:
    """关键词命中了，但后半截是「有限公司 / 投标人名称」这类表格残留。"""
    n = compact_title(title)
    if classify_kind(n) == "unknown":
        return False
    if n.endswith("有限公司") or n.endswith("股份有限公司"):
        return True
    if n.endswith("公司") and not n.endswith("有限公司"):
        return True
    if "投标人名称" in n:
        return True
    return False


def _clean_item(raw: str) -> str:
    text = (raw or "").strip().strip("·•-—_ ")
    text = _PAGE_TAIL.sub("", text).strip()
    text = _ITEM_PREFIX.sub("", text).strip(" :：.．、；;。")
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"致$", "", text)
    if not text or _SKIP_SUB.search(text):
        return ""
    if len(text) > 32 or len(text) < 2:
        return ""
    if text.isdigit():
        return ""
    return text
