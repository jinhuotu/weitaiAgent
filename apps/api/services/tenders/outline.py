"""从招标/邀请书正文抽出「投标/响应文件格式」组卷大纲。规则优先，不写承诺函正文。"""

from __future__ import annotations

import re

from api.services.tenders.classifier import is_chapter_title
from api.services.tenders.schema import BidBrief, OUTLINE_KINDS, OutlineItem

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
    r"附(?:件)?[（(]?[一二三四五六七八九十0-9]+[)）]?"
    r"|[（(][一二三四五六七八九十0-9]+[)）]"
    r"|[一二三四五六七八九十]{1,2}、"
    r"|[0-9]{1,2}[.．、]"
    r")[、.．:：\s]*"
)

_ATTACH_HINT = re.compile(r"附(?:件)?[（(]?[一二三四五六七八九十0-9]+")
_ATTACH_SEQ = re.compile(r"附(?:件)?[（(]?([一二三四五六七八九十0-9]+)[)）]?")
_DOC_TAIL = re.compile(r"(?:空白)?(?:稿|模板|(?<!组织)格式)$")
_GLUE_ATTACH = re.compile(
    r"(?<=[日号。；;）)\s])(?=附(?:件)?[（(]?[一二三四五六七八九十0-9]+)"
)
_GLUE_ATTACH_MID = re.compile(r"(附件)(?=附[一二三四五六七八九十0-9])")
_GLUE_YMD_ATTACH = re.compile(r"(年\s*月\s*日)(?=附)")
_GLUE_TEMPLATE_TITLE = re.compile(
    r"(模板|空白稿|格式)(?=(?:投标|响应)?(?:承诺函|承诺书|投标函|响应函))"
)
_GLUE_LETTER_ZHI = re.compile(
    r"((?:投标|响应)?(?:承诺函|承诺书|投标函|响应函))(?=致[：:])"
)
_GLUE_AFTER_TITLE = re.compile(
    r"(清单及说明|功能需求清单|报价单模板|报价单)(?=[一二三四五六七八九十]、)"
)
_GLUE_CO_BODY = re.compile(
    r"(有限责任公司|股份有限公司|有限公司)(?=我方|兹)"
)
_GLUE_QUOTE_SEQ = re.compile(
    r"(（[0-9一二三四五六七八九十]+）[^|\n]{0,20}报价)(?=序号)"
)

_PAGE_TAIL = re.compile(r"[\s.·•…]*[0-9]{1,4}$")

_SKIP_SUB = re.compile(
    r"(递交|时间|地点|页码|密封|份数|正本|副本|电子版|电子标书|扫描版|可编辑|"
    r"同步提供|盖章|签字|我方承诺|我方在此|应当逐条|如响应文件|响应供应商根据|"
    r"账号|账户|开户行|汇款|收款账号|评标结束|退还保证金|视为无效)"
)

_FIELD_KV = re.compile(r"[：:]")
_CLAUSE_START = re.compile(r"^(具有|具备|应当|必须|不得|需具备)")
_CONTACT = re.compile(r"(监督人|联系人|联系电话|联系方式|手机号|传真|经办人)")
_PAGE_MARK = re.compile(r"^\[?第\s*\d+\s*页\]?$|^-?\d{2,4}-$")
_COMPANY_ONLY = re.compile(
    r"^[\u4e00-\u9fff]{2,24}(?:有限责任公司|股份有限公司|有限公司)$"
)
_MASHED_ZHI = re.compile(
    r"^((?:[一二三四五六七八九十]+、)?(?:合同条款)?响应书|"
    r"(?:投标|响应)?承诺书|承诺函|投标函|响应函)(致[：:]?.*)$",
    re.M,
)
_ZHI_BODY = re.compile(r"(我公司|我方现|我方确认|我方已|如下承诺|现做出如下|保证投标|并重申)")
_CO_THEN_BODY = re.compile(
    r"^(?P<co>.+?(?:有限责任公司|股份有限公司|有限公司))(?P<body>(?:我方|兹).+)$"
)
_SIGN_HEAD = re.compile(
    r"(投标人名称|供应商名称|法定代表人或授权代表|法定代表人或其委托代理人|"
    r"法定代表人或委托代理人|法定代表人|委托代理人|授权代表|"
    r"投标人|供应商|联系人|联系电话|日期)"
)
_PHONE = re.compile(r"\d{7,}")
_FIELD_LABEL = re.compile(
    r"^(招标编号|项目编号|采购编号|招标单位|采购人|项目名称|投标人名称|"
    r"供应商名称|日期|地址|授权期间|授权期限|委托期限|单位名称)$"
)
_NOT_DOC_HEAD = re.compile(r"(资格要求|资格条件|符合性要求|评分办法|评标办法)$")

# 目录标题才有的材料名；不用单字「书/证明/承诺/报价」，避免正文句子误中。
_DOC_NOUN = re.compile(
    r"(?:函|清单|资料|保函|执照|证书|备案|保证金|竞标书|竞标|"
    r"承诺书|承诺函|响应书|授权书|委托书|报价表|报价单|报价清单|"
    r"身份证明|资质证明|业绩证明|资格证明|偏离|工具表|"
    r"模板|功能需求|"
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
# 空白稿表头常把「商务偏离表」和「招标项目：」粘成一行
_FORM_TITLE_TAIL = re.compile(r"(招标项目|采购项目|投标项目|项目名称|货物名称)$")
_ONCE_KINDS = frozenset({"biz_dev", "tech_dev", "legal_id", "auth"})
_LETTER_NUM = re.compile(r"^[（(]?[一二三四五六七八九十0-9]+[)）]?[、.．]?")
_SKIP_STMT = re.compile(r"(中小企业声明|残疾人福利)")
# 招标书第八章空白稿/须知：不是商务标要交的填好的页
_PACK_SKIP = re.compile(
    r"(中小企业声明|残疾人福利|"
    r"资格审查资料|资格审查其他|"
    r"投标人基本情况表|近年财务状况表|"
    r"正在施工.{0,12}新承接|"
    r"国家企业信用信息公示|"
    r"主要项目管理人员简历|项目经理简历表|项目经理主要工作经历|"
    r"项目管理机构组成表|^项目管理机构$|"
    r"拟分包项目情况|^拟分包$|"
    r"暗标|装订要求|"
    r"劳动力计划表|临时用地|"
    r"其他有必要提供|^其他资料$|^其他材料$)"
)
_PACK_CUT = re.compile(
    r"(?:^|\n)\s*[（(]?[一二三四五六七八九十0-9]*[)）]?[、.．]?\s*"
    r"(?:资格审查资料|资格审查其他材料|其他资料|"
    r"中小企业声明|残疾人福利|施工组织设计|"
    r"主要项目管理人员简历|拟分包项目情况|项目管理机构|"
    r"暗标|编制及装订|劳动力计划表)"
)
_WORKS_MARK = ("施工组织", "项目管理机构", "拟分包", "承包工程", "日历天")
_APP_SPLIT = re.compile(
    r"(?:^|\n)\s*[（(]?[二2][)）]?\s*[、.．]?\s*(?:投标函附录|响应函附录)"
    r"|(?:^|\n)\s*(?:投标函附录|响应函附录)"
)
_QUOTE_DEBRIS = re.compile(r"(报价表说明|报价表单位|人民币元)")
_INSTR_TITLE = re.compile(
    r"(满足第|按照.{0,12}格式|格式自拟|须对所提供|详见下表|计分规则|共计\d+分|的评审|提供详细的技术文件)"
)
_SEAL_KIND = "seal_reg"

_AUTH_OPTIONAL = re.compile(
    r"法定代表人亲自.{0,24}(可不提供|不必提供|无需提供|可以不提供).{0,12}授权"
    r"|不(另行)?委托.{0,16}(可不提供|无需提供).{0,12}授权"
    r"|授权委托书.{0,16}(可不提供|非必须|不是必须)"
)
_AUTH_REQUIRED = re.compile(
    r"(必须|须|应当)(提供|出具|提交).{0,12}授权委托"
    r"|授权委托书.{0,24}(必须提供|未提供.{0,10}废标|否则.{0,8}废标)"
    r"|不得由法定代表人亲自"
    r"|(委托|授权).{0,24}(递交|开标|参加开标|出席开标)"
    r"|(递交|开标).{0,20}(委托代理人|授权代表|授权委托)"
    r"|须.{0,8}(委托代理人|授权代表).{0,16}(递交|参加|出席|开标)"
)

_GENERATE_KINDS = frozenset(
    {
        "letter",
        "legal_id",
        "auth",
        "quote",
        "biz_dev",
        "tech_dev",
        "performance",
        "factory",
        "tech_plan",
        "tech_chapter",
    }
)

_TECH_CHAPTER_MARKS = (
    "施工组织",
    "组织设计",
    "质量保证",
    "质量管理",
    "质量体系",
    "质量措施",
    "安全管理",
    "安全文明",
    "安全保证",
    "进度计划",
    "工期保证",
    "工期措施",
    "保证体系",
    "技术规格",
    "技术措施",
    "技术方案",
    "新工艺",
    "信息化监控",
    "BIM",
    "风险管理",
    "风险措施",
    "环境保护",
    "文明施工",
    "售后服务",
    "培训方案",
    "培训计划",
    "竣工验收",
    "调试验收",
    "运维方案",
    "运维服务",
    "人员配备",
    "拟投入资源",
    "劳动力计划",
    "施工总平面",
    "总平面布置",
    "建造方式",
)

_CHAPTER_HEAD = re.compile(r"^第[一二三四五六七八九十百零〇0-9]+章[^\n]{0,48}")
_LEVEL3 = re.compile(r"^\d+\.\d+\.\d+")
_LEVEL2_DOT = re.compile(r"^\d+\.\d+")
_LEVEL2_PAREN = re.compile(r"^[（(][一二三四五六七八九十0-9]+[)）]")
_LEVEL1_CN = re.compile(r"^[一二三四五六七八九十]{1,2}、")
_LETTER_EXTRAS = frozenset({"quote", "tech_dev", "biz_dev"})
_QUAL_KINDS = frozenset({"scan", "company"})


def compact_title(text: str) -> str:
    return re.sub(r"[\s/／|｜]+", "", text or "")


def toc_title_key(text: str) -> str:
    """仅供目录与正文对齐；不改显示标题、分类或层级。"""
    title = _ITEM_PREFIX.sub("", (text or "").strip())
    key = re.sub(r"[\s、，,：:／/|｜]+", "", title)
    if re.fullmatch(r"(?:技术(?:和|与|及)?商务|商务(?:和|与|及)?技术)(?:差异|偏差|偏离)表", key):
        return "商务和技术偏差表"
    return key


def bid_item_title(title: str) -> str:
    t = (title or "").strip()
    t = re.sub(r"^附(?:件)?[（(]?[一二三四五六七八九十0-9]+[)）]?[：:、.\s]*", "", t)
    t = _DOC_TAIL.sub("", t).strip(" ：:、")
    n = compact_title(t)
    if "营业执照" in n and ("注册地址" in n or "注册住所" in n):
        return "营业执照"
    return t or (title or "").strip()


def _peel_form_suffix(title: str) -> str:
    n = compact_title(title)
    m = _FORM_TITLE_TAIL.search(n)
    if not m:
        return title
    head = n[: m.start()]
    if classify_kind(head) == "unknown":
        return title
    return head


def _outline_key(title: str) -> str:
    n = compact_title(_peel_form_suffix(title))
    n = _ATTACH_HINT.sub("", n)
    n = re.sub(r"^附(?:件)?", "", n)
    n = re.sub(r"[（）()：:、.．]", "", n)
    n = _DOC_TAIL.sub("", n)
    return n or compact_title(title)


def _is_buyer_spec_title(n: str) -> bool:
    if "技术标准" in n and "要求" in n:
        return True
    if "功能需求" in n:
        return True
    return False


def _is_tech_chapter_title(n: str) -> bool:
    if any(k in n for k in ("装订", "暗标", "编制及", "使用说明", "须知")):
        return False
    from api.services.tenders.categories import title_goes_technical

    if title_goes_technical(n):
        return True
    if any(k in n for k in _TECH_CHAPTER_MARKS):
        return True
    if n.endswith("方案") and "报价" not in n:
        return True
    if n.endswith("图纸") or n.endswith("设计图"):
        return True
    return False


def classify_kind(title: str) -> str:
    n = compact_title(title)
    if not n:
        return "unknown"
    if _is_clause_title(n):
        return "unknown"
    if any(
        k in n
        for k in (
            "指定的接收人",
            "发包人指定",
            "承包人指定",
            "监理人指定",
            "发包人授权",
            "承包人授权",
            "监理人授权",
        )
    ):
        return "unknown"
    if re.search(r"\.html|操作手册|电子保函操作", n, re.I):
        return "unknown"
    if "商务" in n and ("偏离" in n or "偏差" in n) and "技术" not in n:
        return "biz_dev"
    if ("技术" in n or "商务" in n) and ("偏离" in n or "偏差" in n):
        return "tech_dev"
    if "偏离表" in n or "偏差表" in n or n.endswith("偏离"):
        return "tech_dev"
    if any(k in n for k in ("身份证明", "身份证复印件", "法定代表人身份", "负责人身份")):
        return "legal_id"
    if "授权" in n and ("委托" in n or n.endswith("授权书")):
        return "auth"
    if re.search(r"(报价表说明|报价表单位|人民币元)", n):
        return "unknown"
    if any(k in n for k in ("报价清单", "报价表", "报价单", "分项报价", "工程量清单")):
        return "quote"
    if any(k in n for k in ("投标函附录", "响应函附录")):
        return "letter"
    if any(k in n for k in ("投标函", "响应函")):
        return "letter"
    if "响应书" in n:
        return "commitment_copy"
    if any(k in n for k in ("承诺函", "承诺书")):
        return "commitment_copy"
    if any(k in n for k in ("类似业绩", "企业业绩", "合同业绩", "类似项目", "业绩证明", "业绩要求")):
        return "performance"
    if "公司简介" in n or "企业简介" in n:
        return "company"
    if "原厂" in n:
        return "factory"
    if "实施方案" in n or "响应方案" in n or n.endswith("技术标") or "技术标（" in n or "技术标(" in n:
        return "tech_plan"
    if _is_buyer_spec_title(n):
        return "unknown"
    if _is_tech_chapter_title(n):
        return "tech_chapter"
    if any(
        k in n
        for k in (
            "营业执照",
            "资质证书",
            "资质证明",
            "企业资质",
            "资质文件",
            "扫描件",
            "资格证明",
            "资格审查资料",
            "保证金",
            "保函",
            "财务报表",
            "财务审计",
            "财务报告",
            "财务要求",
            "审计报告",
            "其它文件",
            "其他文件",
            "其它资料",
            "其他资料",
        )
    ):
        return "scan"
    if any(k in n for k in ("税务信息", "关联关系", "企业基本情况", "供应商基本情况", "印鉴", "备案表")):
        return "company"
    return "unknown"


def is_seal_register(item: OutlineItem | None = None, title: str = "") -> bool:
    t = title or (item.title if item is not None else "") or ""
    n = compact_title(t)
    if any(k in n for k in ("印鉴", "印件")):
        return True
    return "备案表" in n and any(k in n for k in ("章", "证件", "印章"))


def _prefer_seal_title(left: str, right: str) -> str:
    a, b = (left or "").strip(), (right or "").strip()
    an, bn = compact_title(a), compact_title(b)

    def score(n: str) -> int:
        if not n:
            return -99
        s = 0
        if "印鉴预留备案表" in n:
            s += 10
        elif "印鉴" in n:
            s += 4
        if "备案表" in n:
            s += 2
        if n.startswith("备注") or "红色章" in n:
            s -= 20
        s -= min(len(n), 40) // 5
        return s

    sa, sb = score(an), score(bn)
    if sa != sb:
        return a if sa > sb else b
    return a or b


def looks_like_quote_form(text: str) -> bool:
    n = compact_title(text)
    if not n:
        return False
    if n.count("序号") >= 2:
        return True
    if "项目内容" in n and "金额" in n:
        return True
    if "报价单模板" in n or "分项明细" in n:
        return True
    return False


def _keep_unknown_title(title: str) -> bool:
    return _is_bidder_submit_title(title)


def _format_pack_heading(title: str) -> bool:
    """格式章里的综合标题，如「资格、资信证明文件」「响应方案」。"""
    n = compact_title(title)
    if not n or len(n) > 24:
        return False
    if any(k in n for k in ("审查", "声明", "简历", "装订", "暗标")):
        return False
    if "资格" in n and "证明" in n:
        return True
    if "资信" in n and ("证明" in n or "文件" in n):
        return True
    return any(k in n for k in ("响应方案", "响应书", "偏差表", "差异表", "偏离表"))


def source_for_kind(kind: str, *, skipped: bool = False) -> str:
    if skipped:
        return "skip"
    if kind in _GENERATE_KINDS:
        return "generate"
    return "copy"


def extract_auth_need(text: str, items: list[OutlineItem] | None = None) -> str:
    blob = re.sub(r"\s+", "", text or "")
    if _AUTH_OPTIONAL.search(blob):
        return "optional"
    if _AUTH_REQUIRED.search(blob):
        return "required"
    if any((item.kind or "").strip() == "auth" for item in (items or [])):
        return "optional"
    if "授权委托" in blob:
        return "optional"
    return ""


def apply_auth_outline(
    items: list[OutlineItem],
    *,
    has_agent: bool,
    auth_need: str = "",
    auth_enabled: bool | None = None,
) -> list[OutlineItem]:
    """授权委托书是否入卷：表单开关优先；没开则跳过。"""
    out: list[OutlineItem] = []
    for item in items:
        if (item.kind or "").strip() != "auth":
            out.append(item)
            continue
        if auth_enabled is None:
            skip = not has_agent and (auth_need or "").strip() != "required"
        else:
            skip = not bool(auth_enabled)
        out.append(
            item.model_copy(update={"skipped": skip, "source": source_for_kind("auth", skipped=skip)})
        )
    return out


def _default_volume_pool(brief: BidBrief) -> list[OutlineItem]:
    items = [
        OutlineItem(id="d01", title="投标函", kind="letter", source="generate"),
        OutlineItem(id="d02", title="法定代表人身份证明", kind="legal_id", source="generate"),
        OutlineItem(id="d03", title="授权委托书", kind="auth", source="generate"),
        OutlineItem(id="d04", title="分项报价表", kind="quote", source="generate"),
        OutlineItem(id="d05", title="商务偏离表", kind="biz_dev", source="generate"),
        OutlineItem(id="d06", title="企业业绩", kind="performance", source="generate"),
        OutlineItem(id="d07", title="原厂生产承诺", kind="factory", source="generate"),
        OutlineItem(id="d08", title="技术偏差表", kind="tech_dev", source="generate"),
        OutlineItem(id="d09", title="技术方案", kind="tech_chapter", source="generate"),
    ]
    if brief.includeCommitment:
        items.append(OutlineItem(id="d10", title="投标承诺书", kind="commitment_copy", source="generate"))
    return items


def _ensure_kind(items: list[OutlineItem], kind: str, title: str) -> list[OutlineItem]:
    if any((item.kind or "").strip() == kind and not item.skipped for item in items):
        return items
    items.append(OutlineItem(id=f"v-{kind}", title=title, kind=kind, source="generate", required=True))
    return items


def scoring_chapter_titles(brief: BidBrief) -> list[str]:
    titles: list[str] = []
    seen: set[str] = set()
    for req in brief.bidRequirements or []:
        if (req.category or "").strip() != "scoring":
            continue
        t = (req.targetTitle or "").strip() or (req.text or "").strip()[:24]
        if len(t) < 4:
            continue
        key = compact_title(t)[:40]
        if key in seen:
            continue
        seen.add(key)
        titles.append(t[:40])
        if len(titles) >= 8:
            break
    return titles


def ensure_tech_chapters(items: list[OutlineItem], brief: BidBrief | None = None) -> list[OutlineItem]:
    out = _ensure_kind(list(items), "tech_dev", "技术偏差表")
    if any((item.kind or "").strip() in {"tech_plan", "tech_chapter"} and not item.skipped for item in out):
        return out
    titles = scoring_chapter_titles(brief) if brief is not None else []
    if not titles:
        titles = ["技术方案"]
    for i, title in enumerate(titles):
        out.append(
            OutlineItem(
                id=f"v-tech-{i + 1:02d}",
                title=title,
                kind="tech_chapter",
                source="generate",
                required=True,
            )
        )
    return out


def hydrate_tech_excerpts(items: list[OutlineItem]) -> list[OutlineItem]:
    out: list[OutlineItem] = []
    for item in items:
        kind = (item.kind or "").strip()
        excerpt = (item.inviteExcerpt or "").strip()
        if kind in {"tech_plan", "tech_chapter"} and not excerpt:
            excerpt = (item.body or "").strip()
            item = item.model_copy(update={"inviteExcerpt": excerpt})
        out.append(item)
    return out


# 商务标缺一即废/评委默认要看的页。邀请书有模板就沿用，没有则模块填写或标题+方框。
_BIZ_ESSENTIALS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("letter", "投标函", ()),
    ("legal_id", "法定代表人身份证明", ()),
    ("auth", "授权委托书", ()),
    ("scan", "投标保证金", ("保证金", "保函")),
    ("scan", "营业执照", ("营业执照",)),
    ("scan", "资质证书", ("资质证书", "资质证明", "企业资质", "资质文件")),
    ("scan", "近三年财务报表", ("财务报表", "财务审计", "财务报告", "审计报告")),
    ("performance", "类似项目业绩", ()),
    ("commitment_copy", "无违法承诺函", ("无违法", "无行贿", "无失信")),
    ("biz_dev", "商务偏离表", ()),
)


def _essential_hit(items: list[OutlineItem], kind: str, marks: tuple[str, ...]) -> OutlineItem | None:
    for item in items:
        if (item.kind or "").strip() != kind:
            continue
        if item.skipped:
            if kind == "auth":
                return item
            continue
        if not marks:
            return item
        n = compact_title(item.title)
        if any(m in n for m in marks):
            return item
    return None


def _is_invite_notice(title: str) -> bool:
    """招标须知/评标规则/收款信息，不是投标书章节。"""
    n = compact_title(title or "")
    if not n:
        return False
    if re.search(r"\.html", n, re.I):
        return True
    if any(k in n for k in ("操作手册", "下载《", "电子保函操作", "公共资源交易")):
        return True
    if any(k in n for k in ("招标须知", "投标须知", "投标人须知", "供应商须知", "采购须知")):
        return True
    if any(
        k in n
        for k in (
            "指定的接收人",
            "发包人指定",
            "承包人指定",
            "监理人指定",
            "发包人授权",
            "承包人授权",
            "监理人授权",
        )
    ):
        return True
    if any(k in n for k in ("住房城乡", "住建部", "建设部关于", "进一步降低", "进一步加快")):
        return True
    if any(k in n for k in ("账号", "账户", "开户", "汇款", "收款")):
        return True
    if any(k in n for k in ("评标", "退还", "视为无效", "无效标", "作废标")):
        return True
    if any(k in n for k in ("计分规则", "计分办法", "评分细则", "基准价")):
        return True
    if "见下表" in n or "如下表" in n:
        return True
    if "评审" in n and any(k in n for k in ("分", "计分", "评分")):
        return True
    if "计算规则" in n and "报价表" not in n:
        return True
    if any(k in n for k in ("保证金的形式", "免缴保证金", "质量保证金", "履约保证金")):
        return True
    if "保证金" in n and any(k in n for k in ("形式", "转账", "扣留", "手册", "下载")):
        return True
    if "保证金" in n and re.search(r"\d", n) and any(k in n for k in ("元", "万", "金额")):
        return True
    if "资格审查" in n and any(k in n for k in ("适用于", "预审", "特殊要求")):
        return True
    return False


def _is_clause_title(title: str) -> bool:
    """须知/废标条款，不是组卷材料名。"""
    n = compact_title(title or "")
    if not n:
        return False
    if _is_invite_notice(n):
        return True
    if any(k in n for k in ("备案", "预留", "模板", "清单")):
        return False
    if n.endswith(("表", "函", "书", "件")) and not any(
        k in n for k in ("废标", "无效", "无单位")
    ):
        return False
    if n.startswith(("无", "未", "不得", "禁止", "未加盖", "未提供")) and any(
        k in n for k in ("印鉴", "公章", "签字", "盖章")
    ):
        return True
    if "无单位" in n and any(k in n for k in ("印鉴", "公章")):
        return True
    if "印鉴" in n and "备案" not in n and "预留" not in n:
        if any(k in n for k in ("无单位", "未加盖", "无效", "委托代理人")):
            return True
    if any(k in n for k in ("废标", "否决投标", "按无效", "作无效")):
        return True
    return False


def _biz_sort_key(item: OutlineItem, idx: int) -> tuple[int, int, int]:
    from api.services.tenders.categories import item_volume

    kind = (item.kind or "").strip()
    n = compact_title(item.title)
    if kind in {"tech_plan", "tech_chapter", "tech_dev"} or item_volume(
        kind=kind, title=item.title, key=item.id
    ) == "technical":
        return (20, 0, idx)
    if kind == "unknown":
        return (19, 0, idx)
    if kind == "letter":
        return (0, 1 if "附录" in n else 0, idx)
    if kind == "commitment_copy":
        if any(k in n for k in ("无违法", "无行贿", "无失信")):
            return (4, 8, idx)
        return (1, 0, idx)
    if kind == "legal_id":
        return (2, 0, idx)
    if kind == "auth":
        return (3, 0, idx)
    if kind == "scan":
        if any(k in n for k in ("保证金", "保函")):
            return (4, 0, idx)
        if "营业执照" in n:
            return (4, 1, idx)
        if any(k in n for k in ("资质",)):
            return (4, 2, idx)
        if any(k in n for k in ("财务", "审计")):
            return (4, 3, idx)
        return (4, 5, idx)
    if kind == "company":
        return (5, 0, idx)
    if kind == "performance":
        return (5, 1, idx)
    if kind == "quote":
        return (6, 0, idx)
    if kind == "biz_dev":
        return (7, 0, idx)
    if kind == "factory":
        return (8, 0, idx)
    return (9, 0, idx)


def _format_order_locked(items: list[OutlineItem]) -> bool:
    """原版格式章目录已带一、二、编号时，不再按报价/技术模板重排。"""
    n = 0
    for item in items:
        if item.skipped or not item.tocEligible:
            continue
        raw = re.sub(r"\s+", "", item.rawTitle or "")
        if re.match(r"^[一二三四五六七八九十]{1,3}、", raw):
            n += 1
    return n >= 3


def _order_biz_outline(items: list[OutlineItem]) -> list[OutlineItem]:
    """商务标：承诺函/函件 → 法人证明/授权 → 资质 → 概况与业绩 → 报价 → 偏离。"""
    if _format_order_locked(items):
        return list(items)
    keyed = [(_biz_sort_key(item, i), item) for i, item in enumerate(items)]
    keyed.sort(key=lambda row: row[0])
    return [item for _, item in keyed]


def _is_bidder_submit_title(title: str) -> bool:
    """邀请书里投标人要交的函/表/清单，不是须知条款。"""
    t = (title or "").strip()
    n = compact_title(t)
    if not n or is_outline_junk(title=t):
        return False
    if n.endswith(("函", "表", "清单")):
        return True
    if n.endswith("书") and any(
        k in n for k in ("承诺", "授权", "委托", "响应", "申请", "声明", "报告")
    ):
        return True
    return any(
        k in n
        for k in (
            "联合体",
            "功能需求",
            "报价单模板",
            "项目组成员",
            "发包人代表",
        )
    )


def letter_role(title: str) -> str:
    n = _LETTER_NUM.sub("", compact_title(title or ""))
    if "附录" in n and "及" in n:
        return "pack"
    if "附录" in n:
        return "appendix"
    return "letter"


def is_policy_statement_title(title: str) -> bool:
    return bool(_SKIP_STMT.search(compact_title(title or "")))


def is_invite_pack_title(title: str) -> bool:
    n = compact_title(title or "")
    n = _ITEM_PREFIX.sub("", n)
    n = re.sub(r"[（(]如有[)）]", "", n)
    return bool(_PACK_SKIP.search(n))


def _pack_cut_pos(raw: str, title: str = "") -> int:
    """下一节标题才截。函里「（9）施工组织设计；」是清单，不截。"""
    t = compact_title(title)
    own_org = "施工组织" in t
    for m in _PACK_CUT.finditer(raw):
        if m.start() <= 0:
            continue
        window = raw[max(0, m.start() - 8) : m.end() + 12]
        if re.search(r"[（(][0-9]{1,2}[)）]", window):
            continue
        # 无编号的组成清单行以「；」收尾，不是下一节标题
        nl = raw.find("\n", m.start() + 1)
        line = raw[m.start() : nl if nl >= 0 else len(raw)]
        if line.strip().endswith(("；", ";")):
            continue
        frag = compact_title(m.group(0))
        if t and frag and (t in frag or frag in t):
            continue
        if own_org and any(k in frag for k in ("劳动力", "暗标", "临时用地", "装订", "施工组织设计")):
            continue
        return m.start()
    return -1


def cut_invite_pack_body(text: str, title: str = "") -> str:
    """函/表正文抄到资格审查、声明函、施工组织须知就停，避免整章粘进来。"""
    raw = (text or "").strip()
    if not raw:
        return ""
    pos = _pack_cut_pos(raw, title)
    if pos > 0:
        return raw[:pos].rstrip()
    return raw


def bid_is_works(brief: BidBrief | None = None, text: str = "") -> bool:
    blob = text or ""
    if brief is not None:
        blob += "".join(item.title for item in (brief.outlineItems or []))
        blob += brief.bidContent or ""
        blob += brief.techPlanNote or ""
    n = compact_title(blob)
    return any(k in n for k in _WORKS_MARK) or "工期" in n


def slice_copied_letter_body(body: str, title: str) -> str:
    raw = (body or "").strip()
    if not raw:
        return ""
    role = letter_role(title)
    hit = _APP_SPLIT.search(raw)
    if role == "appendix":
        return raw[hit.start() :].strip() if hit else raw
    if hit:
        return raw[: hit.start()].strip()
    return raw


def _keep_in_bid_outline(item: OutlineItem) -> bool:
    from api.services.tenders.categories import is_volume_label, title_goes_technical

    kind = (item.kind or "").strip()
    if kind in {"tech_plan", "tech_chapter", "tech_dev"}:
        return not is_volume_label(item.title)
    if title_goes_technical(item.title):
        return not is_volume_label(item.title)
    if is_outline_junk(item):
        return False
    if is_policy_statement_title(item.title) or is_invite_pack_title(item.title):
        return False
    if not is_chapter_title(item.title):
        return False
    if kind in {
        "letter",
        "legal_id",
        "auth",
        "quote",
        "biz_dev",
        "scan",
        "performance",
        "commitment_copy",
        "company",
        "factory",
        "tech_plan",
        "tech_chapter",
        "tech_dev",
    }:
        return True
    return _is_bidder_submit_title(item.title)


def ensure_biz_essentials(items: list[OutlineItem]) -> list[OutlineItem]:
    """必备页先铺上；邀请书只留投标人要交的函/表，须知不进目录。"""
    out = [item for item in items if _keep_in_bid_outline(item)]
    for kind, title, marks in _BIZ_ESSENTIALS:
        if _essential_hit(out, kind, marks) is not None:
            continue
        out.append(
            OutlineItem(
                id=f"biz-{kind}-{len(out) + 1:02d}",
                title=title,
                kind=kind,
                source="generate",
                required=True,
            )
        )
    return _order_biz_outline(dedupe_outline_items(out))


def _order_tech_items(items: list[OutlineItem]) -> list[OutlineItem]:
    """技术卷：方案章在前，技术偏离表收尾。只排公司固定模板，不排商务卷。"""
    head: list[OutlineItem] = []
    mid: list[OutlineItem] = []
    tail: list[OutlineItem] = []
    for item in items:
        kind = (item.kind or "").strip()
        if kind == "tech_dev":
            tail.append(item)
        elif kind in {"tech_plan", "tech_chapter"}:
            head.append(item)
        else:
            mid.append(item)
    return head + mid + tail


def _verbatim_volume_items(raw: list[OutlineItem], volume: str) -> list[OutlineItem]:
    """按标书生成：保持抽出的顺序和标题，不补公司固定章节、不重排。"""
    from api.services.tenders.categories import is_volume_label, item_volume, title_goes_technical

    out: list[OutlineItem] = []
    for item in raw:
        if item.skipped or is_volume_label(item.title):
            continue
        if is_policy_statement_title(item.title):
            continue
        kind = (item.kind or "").strip()
        if item_volume(kind=kind, title=item.title, key=item.id) != volume:
            continue
        if (
            volume == "technical"
            and title_goes_technical(item.title)
            and kind not in {"tech_plan", "tech_chapter", "tech_dev"}
        ):
            item = item.model_copy(update={"kind": "tech_chapter"})
        out.append(item)
    return out


def items_for_volume(brief: BidBrief, volume: str) -> list[OutlineItem]:
    """商务标 / 技术标分卷。卷名标题不当正文；商务标补必备页，技术标不发明请书没有的页。"""
    from api.services.tenders.categories import is_volume_label, item_volume, title_goes_technical

    raw = [item for item in (brief.outlineItems or []) if not item.skipped]
    if (brief.layoutMode or "").strip() == "outline":
        return _verbatim_volume_items(raw, volume)
    pool = list(_default_volume_pool(brief))
    pool = apply_auth_outline(
        pool,
        has_agent=bool((brief.agentName or "").strip()),
        auth_need=(brief.authNeed or "").strip(),
        auth_enabled=bool(brief.authEnabled),
    )
    out: list[OutlineItem] = []
    for item in pool:
        if item.skipped:
            continue
        if is_volume_label(item.title):
            continue
        if not _keep_in_bid_outline(item):
            continue
        kind = (item.kind or "").strip()
        if not kind:
            continue
        if item_volume(kind=kind, title=item.title, key=item.id) == volume:
            if (
                volume == "technical"
                and title_goes_technical(item.title)
                and kind not in {"tech_plan", "tech_chapter", "tech_dev"}
            ):
                item = item.model_copy(update={"kind": "tech_chapter"})
            out.append(item)
    out = dedupe_letter_items(dedupe_outline_items(out))
    if volume == "technical":
        out = _order_tech_items(ensure_tech_chapters(out, brief))
    elif volume == "business":
        if not out:
            out = [
                item
                for item in apply_auth_outline(
                    _default_volume_pool(brief),
                    has_agent=bool((brief.agentName or "").strip()),
                    auth_need=(brief.authNeed or "").strip(),
                    auth_enabled=bool(brief.authEnabled),
                )
                if not item.skipped
                and item_volume(kind=item.kind, title=item.title, key=item.id) == "business"
            ]
        if not any((item.kind or "").strip() == "biz_dev" for item in out):
            out = _ensure_kind(out, "biz_dev", "商务偏离表")
    return out


def items_for_full(brief: BidBrief) -> list[OutlineItem]:
    """完整版：按邀请书目录顺序接商务池和技术池。没有目录时商务池在前、技术池在后。"""
    biz = items_for_volume(brief, "business")
    tech = items_for_volume(brief, "technical")
    return _join_volume_pools(brief, biz, tech)


def _join_volume_pools(
    brief: BidBrief,
    biz: list[OutlineItem],
    tech: list[OutlineItem],
) -> list[OutlineItem]:
    pools = list(biz) + list(tech)
    if not pools:
        return []
    catalog = [item for item in (brief.outlineItems or []) if not item.skipped]
    if not catalog:
        return _unique_items(pools)
    by_key: dict[str, OutlineItem] = {}
    by_kind: dict[str, list[OutlineItem]] = {}
    for item in pools:
        key = _outline_key(item.title)
        if key and key not in by_key:
            by_key[key] = item
        kind = (item.kind or "").strip()
        if kind:
            by_kind.setdefault(kind, []).append(item)
    used: set[int] = set()
    ordered: list[OutlineItem] = []

    def take(item: OutlineItem | None) -> None:
        if item is None or id(item) in used:
            return
        ordered.append(item)
        used.add(id(item))

    for raw in catalog:
        hit = by_key.get(_outline_key(raw.title))
        if hit is None or id(hit) in used:
            kind = (raw.kind or "").strip()
            hit = next((it for it in by_kind.get(kind) or [] if id(it) not in used), None)
        take(hit)
    for item in pools:
        take(item)
    return ordered


def _unique_items(items: list[OutlineItem]) -> list[OutlineItem]:
    used: set[int] = set()
    out: list[OutlineItem] = []
    for item in items:
        if id(item) in used:
            continue
        used.add(id(item))
        out.append(item)
    return out


_CANONICAL_TITLE = {
    "legal_id": "法定代表人身份证明",
    "auth": "授权委托书",
    "letter": "投标函",
}


def _prefer_once_item(cur: OutlineItem, prev: OutlineItem) -> OutlineItem:
    kind = (cur.kind or prev.kind or "").strip()
    canon = _CANONICAL_TITLE.get(kind, "")

    def score(it: OutlineItem) -> int:
        n = compact_title(it.title)
        s = 0
        if canon and n == canon:
            s += 24
        elif canon and canon in n:
            s += 8
        s -= min(len(n), 48) // 3
        body = (it.body or "").strip()
        if looks_like_form_template(body) or looks_like_quote_form(body):
            s += 14
        return s

    return cur if score(cur) > score(prev) else prev


def dedupe_outline_items(items: list[OutlineItem]) -> list[OutlineItem]:
    """同一张偏离/报价表只留一条；身份证明/授权书各留一份。"""
    seen_key: set[str] = set()
    seen_kind: set[str] = set()
    out: list[OutlineItem] = []
    for item in items:
        peeled = _peel_form_suffix((item.title or "").strip())
        kind = (item.kind or "").strip()
        if peeled and peeled != item.title:
            item = item.model_copy(update={"title": peeled})
        pretty = bid_item_title(item.title)
        if pretty and pretty != item.title:
            item = item.model_copy(update={"title": pretty})
        key = _outline_key(item.title)
        if kind == "quote":
            prev_i = next(
                (i for i, x in enumerate(out) if (x.kind or "").strip() == "quote"),
                None,
            )
            if prev_i is not None:
                prev = out[prev_i]
                better = _prefer_quote_title(item.title, prev.title)
                body = _prefer_quote_body(item.body, prev.body)
                src = "copy" if looks_like_quote_form(body or "") else (prev.source or item.source)
                out[prev_i] = prev.model_copy(
                    update={
                        "title": better or prev.title,
                        "body": body or prev.body,
                        "source": src,
                    }
                )
                continue
        if key in seen_key:
            continue
        if is_seal_register(item):
            prev_i = next((i for i, x in enumerate(out) if is_seal_register(x)), None)
            if prev_i is not None:
                better = _prefer_seal_title(item.title, out[prev_i].title)
                if better and better != out[prev_i].title:
                    out[prev_i] = out[prev_i].model_copy(update={"title": better})
                continue
            seen_kind.add(_SEAL_KIND)
        if kind in _ONCE_KINDS and kind in seen_kind:
            prev_i = next(
                (i for i, x in enumerate(out) if (x.kind or "").strip() == kind),
                None,
            )
            if prev_i is not None:
                out[prev_i] = _prefer_once_item(item, out[prev_i])
            continue
        seen_key.add(key)
        if kind in _ONCE_KINDS:
            seen_kind.add(kind)
        out.append(item)
    return out


def dedupe_letter_items(items: list[OutlineItem]) -> list[OutlineItem]:
    """整套只留 1 份投标函 + 1 份附录，「函及其附录」不再另开一页。"""
    letter: OutlineItem | None = None
    appendix: OutlineItem | None = None
    rest: list[OutlineItem] = []
    letter_i = appendix_i = -1
    for item in items:
        if (item.kind or "").strip() != "letter":
            rest.append(item)
            continue
        role = letter_role(item.title)
        if role == "appendix":
            if appendix is None:
                appendix = item
                appendix_i = len(rest)
                rest.append(item)
            continue
        if role == "pack":
            # 空的「投标函及投标函附录」只是目录总题。占了名额会把后面的（一）投标函丢掉，再套一页固定函。
            if (item.body or "").strip() and letter is None:
                letter = item.model_copy(
                    update={"title": "投标函" if "响应" not in item.title else "响应函"}
                )
                letter_i = len(rest)
                rest.append(letter)
            elif not (item.body or "").strip():
                rest.append(item)
            continue
        if letter is None:
            letter = item
            letter_i = len(rest)
            rest.append(item)
    raw = (letter.body if letter is not None else "") or ""
    if letter is not None:
        letter.body = slice_copied_letter_body(letter.body, "投标函")
        if 0 <= letter_i < len(rest):
            rest[letter_i] = letter
    app_body = slice_copied_letter_body(raw, "投标函附录")
    if appendix is not None:
        src = (appendix.body or "").strip()
        if (not src) or src == raw or "我方已" in compact_title(src):
            appendix = appendix.model_copy(update={"body": app_body if app_body != (letter.body if letter else "") else src})
        else:
            appendix = appendix.model_copy(update={"body": slice_copied_letter_body(src, "投标函附录")})
        if 0 <= appendix_i < len(rest):
            rest[appendix_i] = appendix
    elif letter is not None and app_body and app_body != (letter.body or ""):
        rest.insert(
            letter_i + 1,
            OutlineItem(
                id=f"{letter.id or 'o'}-app",
                kind="letter",
                title="响应函附录" if "响应" in (letter.title or "") else "投标函附录",
                source="copy",
                required=True,
                body=app_body,
            ),
        )
    return rest


def _same_quote_form(left: str, right: str) -> bool:
    a, b = compact_title(left), compact_title(right)
    if not a or not b:
        return False
    return True


def _prefer_quote_body(left: str | None, right: str | None) -> str:
    a, b = (left or "").strip(), (right or "").strip()
    if looks_like_quote_form(a) and not looks_like_quote_form(b):
        return a
    if looks_like_quote_form(b) and not looks_like_quote_form(a):
        return b
    return a if len(a) >= len(b) else b


def _prefer_quote_title(left: str, right: str) -> str:
    a, b = (left or "").strip(), (right or "").strip()
    an, bn = compact_title(a), compact_title(b)

    def score(n: str) -> int:
        if not n:
            return -99
        s = 10 - min(len(n), 20) // 2
        if n in {"分项报价表", "报价表", "报价单", "投标报价单"}:
            s += 8
        if _QUOTE_DEBRIS.search(n):
            s -= 20
        if len(n) > 22:
            s -= 12
        if "按照" in n or "分期" in n or n.endswith("说明"):
            s -= 10
        return s

    sa, sb = score(an), score(bn)
    if sa != sb:
        return a if sa > sb else b
    return a or b


def _is_quote_blurb(n: str) -> bool:
    if "报价" not in (n or "") or len(n) < 16:
        return False
    return any(k in n for k in ("按照", "分期签订", "报价形式"))


def is_outline_junk(item: OutlineItem | None = None, title: str = "") -> bool:
    t = title or (item.title if item is not None else "") or ""
    if re.search(r"[：:]\s*[/／]\s*$", (t or "").strip()):
        return True
    if _format_pack_heading(t):
        return False
    n = compact_title(t)
    if n and not is_chapter_title(t):
        return True
    if _is_clause_title(n):
        return True
    if _is_invite_notice(n):
        return True
    if _QUOTE_DEBRIS.search(n):
        return True
    if _is_quote_blurb(n):
        return True
    if n.startswith(("供应商名称", "投标人名称")) and _FIELD_KV.search(t):
        return True
    if "技术标准" in n and "要求" in n:
        return True
    if _INSTR_TITLE.search(n):
        return True
    if n.startswith("资格审查资料") or n in {"资格审查", "资格审查资料"}:
        return True
    if is_invite_pack_title(t):
        return True
    return False


def item_level(raw: str) -> int:
    s = (raw or "").strip()
    if _CHAPTER_HEAD.match(s):
        return 2
    if _LEVEL3.match(s):
        return 3
    if _LEVEL2_DOT.match(s) or _LEVEL2_PAREN.match(s):
        return 2
    if _LEVEL1_CN.match(s):
        return 1
    return 1


def _extracted_item_level(raw: str) -> int:
    """目录提取和正文提取共用同一套子标题规则。"""
    return 2 if lettered_heading(raw) or _SUB_MARK.match(raw.strip()) else item_level(raw)


def _indent_level(raw: str) -> int:
    """无编号目录靠行首缩进区分父子。0 表示看不出，交给编号规则。"""
    s = (raw or "").replace("\t", "  ")
    n = len(s) - len(s.lstrip(" \u3000"))
    if n >= 4:
        return 3
    if n >= 2:
        return 2
    return 0


def normalize_subset_outline(
    items: list[OutlineItem], *, source_titles: dict[str, str] | None = None,
) -> list[OutlineItem]:
    """仅供新建子集目录：编号纠错后用实际树深度赋级，不回写原大纲。

    无编号标题保留显式层级；编号标题优先于可能过期的 level。栈保存原始
    层级和实际深度，因此裁掉父章后的层级跳跃不会留下多余缩进。
    """
    from api.services.tenders.format_rules import peel_toc_lead

    stack: list[tuple[int, int, str, tuple[str, ...]]] = []
    out: list[OutlineItem] = []
    for item in items:
        # rawTitle 也可能已被剥成裸标题，不能遮住正文书签或 title 中仍在的编号。
        candidates = [item.rawTitle, (source_titles or {}).get(item.id, ""), item.title]
        title = next((value.strip() for value in candidates if value.strip()), "")
        numbered = r"^(?:第|[一二三四五六七八九十]+、|[（(]|[A-Ha-h][.．、]|\d+[.．])"
        bare = compact_title(peel_toc_lead(title))
        title = next((value.strip() for value in candidates
                      if re.match(numbered, value.strip())
                      and compact_title(peel_toc_lead(value.strip())) == bare), title)
        rank = max(1, int(item.level or 1))
        family = "plain"
        path: tuple[str, ...] = ()
        # 正文首行有编号、目录只有裸标题的旧数据也能纠正。
        if not re.match(numbered, title):
            first = next((line.strip() for line in item.body.splitlines() if line.strip()), "")
            if compact_title(peel_toc_lead(first)) == compact_title(title):
                title = first
        if _TOP_MARK.match(title) or _CHAPTER_HEAD.match(title):
            rank = 1
        elif _SUB_MARK.match(title) or lettered_heading(title):
            rank = 2
            family = "sub"
        elif match := re.match(r"^(\d+(?:\.\d+)+)(?!\d)", title):
            path = tuple(match.group(1).split("."))
            family = "dot"
            rank = min(4, len(path))
            # 中文二级节点下的 1.1 属于真实三级，而非二级的兄弟。
            if rank == 2 and any(kind == "sub" for _, _, kind, _ in stack):
                rank = 3
            parent = next((node for node in reversed(stack) if node[3] == path[:-1]), None)
            if parent is not None:
                rank = parent[0] + 1
        elif _LEVEL2_PAREN.match(title):
            rank = 3 if rank >= 3 or any(kind == "sub" for _, _, kind, _ in stack) else 2
            family = "paren_num"
        elif re.match(r"^\d{1,2}[.．、](?!\d)", title):
            rank = 3 if any(kind == "sub" for _, _, kind, _ in stack) else min(rank, 2)
            family = "number"
        while stack and stack[-1][0] >= rank:
            stack.pop()
        depth = stack[-1][1] + 1 if stack else 1
        out.append(item.model_copy(update={"level": depth}))
        stack.append((rank, depth, family, path))
    return out


def subset_toc_titles(items: list[OutlineItem]) -> list[str]:
    """新建目录按父节点重新计数；不改正文标题和原格式章目录。"""
    from api.services.tenders.format_rules import cn_ordinal, peel_toc_lead

    counts: list[int] = []
    shows: list[str] = []
    for item in items:
        level = item.level
        del counts[level:]
        while len(counts) < level:
            counts.append(0)
        counts[level - 1] += 1
        bare = peel_toc_lead(item.rawTitle or item.title)
        bare = re.sub(r"^\d{1,2}[.．、](?!\d)\s*", "", bare)
        if level == 1:
            label = f"{cn_ordinal(counts[-1])}、"
        elif level == 2:
            label = f"（{cn_ordinal(counts[-1])}）"
        else:
            label = ".".join(str(n) for n in counts) + " "
        shows.append(label + bare)
    return shows


def tech_chapter_titles(text: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for line in (text or "").splitlines():
        s = line.strip()
        match = _CHAPTER_HEAD.match(s)
        if not match:
            continue
        title = re.sub(r"[\s.·•…]+$", "", match.group(0)).strip()[:40]
        if title and title not in seen:
            seen.add(title)
            out.append(title)
    return out


def build_outline_toc_tree(
    items: list[OutlineItem],
    bookmarks: list[str],
    *,
    tech_text: str = "",
) -> list[dict]:
    packed = list(zip(items, bookmarks))
    if any(int(getattr(item, "level", 1) or 1) > 1 for item, _ in packed):
        return _tree_from_levels(packed)
    return _tree_from_groups(packed, tech_text=tech_text)


def _node(title: str, bookmark: str, children: list[dict] | None = None) -> dict:
    item = {"title": title, "bookmark": bookmark}
    if children:
        item["children"] = children
    return item


def _tree_from_levels(packed: list[tuple[OutlineItem, str]]) -> list[dict]:
    roots: list[dict] = []
    stack: list[tuple[int, dict]] = []
    for item, bm in packed:
        lv = max(1, min(int(getattr(item, "level", 1) or 1), 4))
        node = _node(item.title, bm)
        while stack and stack[-1][0] >= lv:
            stack.pop()
        if stack:
            stack[-1][1].setdefault("children", []).append(node)
        else:
            roots.append(node)
        stack.append((lv, node))
    return roots


def _letter_pack_title(run: list[tuple[OutlineItem, str]]) -> str:
    for item, _ in run:
        t = item.title or ""
        if "及" in t and ("函" in t):
            return t
    blob = "".join(item.title for item, _ in run)
    if "响应" in blob:
        return "响应函及响应函附录"
    return "投标函及投标函附录"


def _tech_children(bookmark: str, item: OutlineItem, tech_text: str) -> list[dict]:
    blob = (item.body or "").strip() or (tech_text or "")
    chaps = tech_chapter_titles(blob)
    if len(chaps) >= 2:
        return [_node(title, bookmark) for title in chaps]
    return [_node("文字描述", bookmark), _node("图纸", bookmark)]


def _tree_from_groups(
    packed: list[tuple[OutlineItem, str]],
    *,
    tech_text: str = "",
) -> list[dict]:
    roots: list[dict] = []
    i = 0
    n = len(packed)
    while i < n:
        item, bm = packed[i]
        kind = (item.kind or "").strip()
        if kind in {"tech_plan", "tech_chapter"}:
            roots.append(_node(item.title, bm, _tech_children(bm, item, tech_text)))
            i += 1
            continue
        if kind == "letter":
            run = [(item, bm)]
            j = i + 1
            while j < n and packed[j][0].kind == "letter":
                run.append(packed[j])
                j += 1
            extras: list[tuple[OutlineItem, str]] = []
            while j < n and packed[j][0].kind in _LETTER_EXTRAS:
                extras.append(packed[j])
                j += 1
            if len(run) + len(extras) >= 2:
                children = [_node(it.title, b) for it, b in run]
                extra_nodes = [_node(it.title, b) for it, b in extras]
                if extra_nodes:
                    app = next((c for c in children if "附录" in (c.get("title") or "")), None)
                    if app is not None:
                        app["children"] = extra_nodes
                    else:
                        children.extend(extra_nodes)
                roots.append(_node(_letter_pack_title(run), run[0][1], children))
                i = j
                continue
        if kind == "legal_id" and i + 1 < n and packed[i + 1][0].kind == "auth":
            auth_item, auth_bm = packed[i + 1]
            roots.append(
                _node(
                    "法定代表人身份证明及授权委托书",
                    bm,
                    [_node(item.title, bm), _node(auth_item.title, auth_bm)],
                )
            )
            i += 2
            continue
        if kind in _QUAL_KINDS:
            run = [(item, bm)]
            j = i + 1
            while j < n and packed[j][0].kind in _QUAL_KINDS:
                run.append(packed[j])
                j += 1
            if len(run) >= 2:
                roots.append(
                    _node("资格审查资料", bm, [_node(it.title, b) for it, b in run])
                )
                i = j
                continue
        roots.append(_node(item.title, bm))
        i += 1
    return roots


def is_noise_title(title: str) -> bool:
    """空白模板里的页眉、填空、正文句子，不是组卷目录条目。"""
    n = compact_title(title)
    if not n:
        return True
    if classify_kind(title) in {"tech_plan", "tech_chapter", "tech_dev"}:
        return False
    if is_outline_junk(title=title):
        return True
    if (
        _FIELD_KV.search(title)
        and classify_kind(title) == "unknown"
        and "模板" not in n
        and "清单" not in n
        and "功能需求" not in n
    ):
        return True
    if n.startswith("备注") or "红色章" in n:
        return True
    if _QUOTE_DEBRIS.search(n):
        return True
    if _is_quote_blurb(n):
        return True
    if n.startswith(("供应商名称", "投标人名称")) and _FIELD_KV.search(title or ""):
        return True
    if "技术标准" in n and "要求" in n:
        return True
    if _INSTR_TITLE.search(n):
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


_GLUE_ENUM = re.compile(r"[一二三四五六七八九十]{1,2}、")
_LETTER_HEAD = re.compile(r"^([A-Ha-h])[.．、]\s*(.+)$")
_ENUM_TITLE = re.compile(
    r"^([一二三四五六七八九十]{1,2}、.{1,18}?"
    r"(?:证明文件|响应书|偏差表|差异表|偏离表|报价表|方案|资料|文件|函|表|书))"
    r"(.*)$"
)
_ENUM_PREFIX = re.compile(r"^([一二三四五六七八九十]{1,2}、)")


def _explode_enum_lines(text: str) -> str:
    """目录粘成一行、标题粘在正文前时拆开，后面才能按原顺序收。"""
    s = re.sub(
        r"(?<=[年月日。；;）)])([一二三四五六七八九十]{1,2}、)",
        r"\n\1",
        text or "",
    )
    out: list[str] = []
    for line in s.splitlines():
        marks = list(_GLUE_ENUM.finditer(line))
        pieces = [line]
        if len(marks) >= 2:
            pieces = []
            if marks[0].start() > 0:
                pre = line[: marks[0].start()].strip()
                if pre:
                    pieces.append(pre)
            for i, mark in enumerate(marks):
                end = marks[i + 1].start() if i + 1 < len(marks) else len(line)
                chunk = line[mark.start() : end].strip()
                if chunk:
                    pieces.append(chunk)
        for piece in pieces:
            match = _ENUM_TITLE.match(piece.strip())
            if not match:
                if piece.strip():
                    out.append(piece.strip())
                continue
            head, rest = match.group(1).strip(), (match.group(2) or "").strip()
            # 「响应函及响应函附录」里的「函」不是标题结束，别拆。
            if rest[:1] in "及和与或（(、/" or len(compact_title(rest)) < 8:
                out.append(piece.strip())
                continue
            out.append(head)
            out.append(rest)
    return "\n".join(out)


def lettered_heading(raw: str) -> tuple[str, str] | None:
    """A.公司简介 / C.业绩要求：长说明 → (短标题, 说明正文)。"""
    match = _LETTER_HEAD.match((raw or "").strip())
    if not match:
        return None
    label = match.group(1).upper() + "."
    rest = re.sub(r"\s+", "", match.group(2)).strip("。")
    if len(rest) < 2:
        return None
    head = re.split(r"[：:]", rest, maxsplit=1)[0]
    if len(head) > 18:
        head = head[:18]
    title = f"{label}{head}"
    body = rest if re.search(r"[：:]", match.group(2)) else ""
    return title, body


def unmash_invitation_text(text: str) -> str:
    """OCR/PDF 常把「附一：模板」粘在日期或上一节末尾，拆开才能挂正文。"""
    s = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    s = _GLUE_ATTACH_MID.sub(r"\1\n", s)
    s = _GLUE_YMD_ATTACH.sub(r"\1\n", s)
    s = _GLUE_ATTACH.sub("\n", s)
    s = _GLUE_TEMPLATE_TITLE.sub(r"\1\n", s)
    s = _GLUE_LETTER_ZHI.sub(r"\1\n", s)
    s = _GLUE_AFTER_TITLE.sub(r"\1\n", s)
    s = _GLUE_CO_BODY.sub(r"\1\n", s)
    s = _GLUE_QUOTE_SEQ.sub(r"\1\n", s)
    return s


_TOP_MARK = re.compile(r"^([一二三四五六七八九十]{1,3})、")
_SUB_MARK = re.compile(r"^[（(]([一二三四五六七八九十]+)[)）]")
_ANY_MARK = re.compile(
    r"[一二三四五六七八九十]{1,3}、|[（(][一二三四五六七八九十0-9]+[)）]"
)


def _explode_short_marks(text: str) -> str:
    """目录粘在一行时拆开。每段不是短标题就不拆，避免把正文句子切开。"""
    out: list[str] = []
    for line in (text or "").splitlines():
        marks = list(_ANY_MARK.finditer(line))
        if len(marks) < 2:
            if line.strip():
                out.append(line.strip())
            continue
        pieces: list[str] = []
        if marks[0].start() > 0:
            pre = line[: marks[0].start()].strip()
            if pre:
                pieces.append(pre)
        for i, mark in enumerate(marks):
            end = marks[i + 1].start() if i + 1 < len(marks) else len(line)
            chunk = line[mark.start() : end].strip().strip("。；; ")
            if chunk:
                pieces.append(chunk)
        if any(_ANY_MARK.match(p) and len(compact_title(p)) > 30 for p in pieces):
            if line.strip():
                out.append(line.strip())
            continue
        out.extend(pieces)
    return "\n".join(out)


def _catalog_heading(line: str) -> str:
    s = re.sub(r"\s+", "", (line or "").strip())
    s = re.sub(r"^目录", "", s)
    s = s.strip("。；; ")
    if not s or s.endswith(("；", ";", "/", "／")):
        return ""
    if re.search(r"[：:][/／]", s):
        return ""
    glued = re.match(
        r"^(.+?(?:附录|身份证明|委托书|保证金|工程量清单|管理机构|资料|简历表|承诺书))"
        r"(?=投标人名称|项目名称|投标范围|大写|单位性质)",
        s,
    )
    if glued:
        s = glued.group(1)
    if not (_TOP_MARK.match(s) or _SUB_MARK.match(s)):
        return ""
    if len(s) > 30:
        return ""
    return s


def _catalog_entries(body: str) -> tuple[list[str], str]:
    """格式章开头的目录。一级标题重复出现时，后面就是正文，目录到此为止。"""
    lines = [ln.strip() for ln in _explode_short_marks(body).splitlines() if ln.strip()]
    best: list[str] = []
    best_end = -1
    best_i = 10**9
    for i, line in enumerate(lines):
        if not (_catalog_heading(line) and _TOP_MARK.match(_catalog_heading(line))):
            continue
        entries: list[str] = []
        seen_top: set[str] = set()
        gap = 0
        j = i
        while j < len(lines) and j - i < 80:
            head = _catalog_heading(lines[j])
            if not head:
                gap += 1
                if len(seen_top) >= 4 and gap >= 3:
                    break
                j += 1
                continue
            gap = 0
            top = _TOP_MARK.match(head)
            if top:
                num = top.group(1)
                if num in seen_top:
                    break
                seen_top.add(num)
            entries.append(head)
            j += 1
        # 目录在格式章最前面。从「二、」起算会把后面的正文「一、」又吞进来，取更靠前的那份。
        if len(seen_top) >= 4 and i < best_i:
            best = entries
            best_end = j
            best_i = i
    if len({(_TOP_MARK.match(h).group(1) if _TOP_MARK.match(h) else "") for h in best}) < 4:
        return [], body
    return best, "\n".join(lines[best_end:])


def _front_children(rest: str, first_top: str) -> list[str]:
    """目录没写到的 A. / （一）只收第一节正文里的，不收函内（1）（2）条款。"""
    kids: list[str] = []
    started = False
    first_num = _TOP_MARK.match(first_top)
    num0 = first_num.group(1) if first_num else ""
    for line in (rest or "").splitlines():
        head = _catalog_heading(line)
        top = _TOP_MARK.match(head) if head else None
        if top:
            if started:
                break
            if top.group(1) == num0:
                started = True
            continue
        if not started:
            continue
        lettered = lettered_heading(line)
        if lettered:
            kids.append(lettered[0])
            continue
        if head and _SUB_MARK.match(head):
            kids.append(head)
    return kids


def _use_body_title(catalog: str, body_title: str) -> str:
    if not body_title or len(body_title) < len(catalog):
        return catalog
    if any(k in body_title for k in ("投标人名称", "项目名称", "投标范围", "大写", "单位性质")):
        return catalog
    return body_title


def _items_from_catalog(entries: list[str]) -> list[OutlineItem]:
    items: list[OutlineItem] = []
    for head in entries:
        src = head.strip()
        lettered = lettered_heading(src)
        shown = lettered[0] if lettered else src
        title = _clean_item(shown) or shown
        if is_policy_statement_title(shown) or is_policy_statement_title(title):
            continue
        kind = classify_kind(title)
        kind = kind if kind in OUTLINE_KINDS else "unknown"
        # 格式章目录里的一行就是一节。rawTitle 留着，前端才不会当噪声删掉。
        items.append(
            OutlineItem(
                id=f"o{len(items) + 1:02d}",
                title=title,
                rawTitle=shown,
                kind=kind,
                source=source_for_kind(kind),
                required=True,
                skipped=False,
                tocEligible=True,
                level=_indent_level(head) or _extracted_item_level(shown),
                body=(lettered[1] if lettered else ""),
            )
        )
    return items


_PLAIN_TOC_HEAD = re.compile(r"^目\s*录$")


def _plain_toc_entries(body: str) -> tuple[list[str], str]:
    """Word 版格式章的目录常没有一、二、编号：认「目录」行后连续的裸标题行。"""
    # 只去行尾空白，行首空格是子目录缩进。
    lines = [ln.rstrip() for ln in (body or "").splitlines()]
    best: list[str] = []
    best_end = -1
    for i, ln in enumerate(lines):
        if not _PLAIN_TOC_HEAD.match(ln):
            continue
        entries: list[str] = []
        seen: set[str] = set()
        j = i + 1
        while j < len(lines) and j - i <= 40:
            s = lines[j]
            key = compact_title(s)
            title = _clean_item(s)
            if not title or is_policy_statement_title(s) or (key and key in seen):
                break
            seen.add(key)
            entries.append(s)
            j += 1
        if len(entries) > len(best):
            best = entries
            best_end = j
    if len(best) < 6:
        return [], body
    return best, "\n".join(lines[best_end:])


def _outline_from_directory(body: str) -> list[OutlineItem]:
    entries, rest = _catalog_entries(body)
    plain = False
    if not entries:
        entries, rest = _plain_toc_entries(body)
        plain = True
    if not entries:
        return []
    body_title: dict[str, str] = {}
    for line in rest.splitlines():
        head = _catalog_heading(line)
        top = _TOP_MARK.match(head) if head else None
        if top and top.group(1) not in body_title:
            body_title[top.group(1)] = head
    merged: list[str] = []
    for head in entries:
        top = _TOP_MARK.match(head)
        if top:
            merged.append(_use_body_title(head, body_title.get(top.group(1), "")))
        else:
            merged.append(head)
    first = next((h for h in merged if _TOP_MARK.match(h)), "")
    kids = _front_children(rest, first) if first else []
    ahead = _lettered_ahead(body, first) if first else []
    # 无编号目录收的是全量条目，正文里的附表不再另挂，避免混成目录项
    tail = [] if plain else _trailing_attach(rest)
    seen = {compact_title(h) for h in merged}
    if kids and first:
        at = next(i for i, h in enumerate(merged) if h == first) + 1
        extra = [k for k in kids if compact_title(k) not in seen]
        merged[at:at] = extra
        seen.update(compact_title(k) for k in extra)
    lead = [k for k in ahead if compact_title(k) not in seen]
    merged = lead + merged
    seen.update(compact_title(k) for k in lead)
    merged.extend(k for k in tail if compact_title(k) not in seen)
    return _items_from_catalog(merged)


def _lettered_ahead(body: str, first_top: str) -> list[str]:
    """目录前面的 A. / B. 也是格式条目，例如资格证明里的营业执照。"""
    out: list[str] = []
    for line in _explode_short_marks(body).splitlines():
        head = _catalog_heading(line)
        if head and head == first_top:
            break
        lettered = lettered_heading(line)
        if lettered:
            out.append(lettered[0])
    return out


def _trailing_attach(rest: str) -> list[str]:
    """编号目录后面单独的「附二：…」，不是正文里重复的一级标题。"""
    out: list[str] = []
    for line in (rest or "").splitlines():
        s = line.strip()
        if not s:
            continue
        head = _catalog_heading(s)
        if head and _TOP_MARK.match(head):
            break
        if is_noise_title(s) or is_outline_junk(title=s):
            continue
        if not re.match(r"^附", s):
            if len(compact_title(s)) > 40:
                break
            continue
        title = _clean_item(s) or s
        if is_noise_title(title) or is_outline_junk(title=title):
            continue
        out.append(s if len(s) <= 40 else title)
    return out


def is_outline_chapter(item: OutlineItem) -> bool:
    """Chapter semantics, independent of form filling and required attachments."""
    from api.services.tenders.format_rules import peel_toc_lead

    raw = compact_title(item.title).strip('：:')
    title = compact_title(peel_toc_lead(item.title)).strip('：:')
    title = re.sub(r'^\d+(?:[.．]\d+)*[、.．]?', '', title)
    if item.skipped or not title or len(title) > 60:
        return False
    # 从格式章目录收进来的条目按章节留在目录里，不再被表单黑名单丢掉。
    if item.tocEligible is True:
        return True
    if item.tocEligible is False:
        return False
    denied = (r'^(?:投标函|响应函|附表|设备表|已标价的?工程量清单|已标价工程量清单|'
              r'执业资格证明|类似项目名称|企业资质|营业执照|中小企业声明函)')
    if re.match(denied, title) or re.match(denied, raw):
        return False
    if title in {'资格证明', '资质证明', '资质证书', '法定代表人身份证明', '授权委托书'}:
        return False
    # Real grouping chapters are distinct from a certificate/form of the same name.
    if re.search(r'(?:资格|商务|技术|承诺|报价).{0,6}(?:文件|部分|资料)$', title):
        return True
    return item.kind in {'unknown', 'tech_plan', 'tech_chapter'} and not is_outline_junk(item)


def mark_outline_chapters(items: list[OutlineItem]) -> list[OutlineItem]:
    for item in items:
        item.tocEligible = is_outline_chapter(item)
    return items


def extract_outline(text: str) -> tuple[str, list[OutlineItem]]:
    """从邀请书/招标书正文抽出格式章节条目。找不到则返回空列表，不报错。"""
    raw = unmash_invitation_text(text or "")
    chapter, body = _format_section(raw)
    if body:
        catalog_items = _outline_from_directory(body)
        if catalog_items:
            letter_body = {item.id: item.body for item in catalog_items if (item.body or "").strip()}
            catalog_items = dedupe_outline_items(catalog_items)
            _attach_item_bodies(catalog_items, body)
            for item in catalog_items:
                kept = letter_body.get(item.id) or ""
                if kept:
                    item.body = kept
            return chapter, mark_outline_chapters(catalog_items)
        body = _explode_enum_lines(body)
    lines = _candidate_lines(body or raw, whole_doc=not bool(body))
    items: list[OutlineItem] = []
    seen: set[str] = set()
    enum_at: dict[str, int] = {}
    dropped: set[int] = set()
    for title, raw_line in lines:
        prefix_m = _ENUM_PREFIX.match((raw_line or "").strip())
        prefix = prefix_m.group(1) if prefix_m else ""
        key = _outline_key(title)
        replacing = bool(prefix and prefix in enum_at)
        if len(key) < 2 or (key in seen and not replacing):
            continue
        kind = classify_kind(title)
        kind = kind if kind in OUTLINE_KINDS else "unknown"
        lettered = lettered_heading(raw_line)
        if kind not in {"tech_plan", "tech_chapter", "tech_dev"} and lettered is None:
            pack = _format_pack_heading(title)
            if not pack and (is_outline_junk(title=title) or not is_chapter_title(title)):
                continue
        if kind == "unknown" and lettered is None and not _keep_unknown_title(title):
            if not _format_pack_heading(title):
                continue
        # 目录和正文会各写一次「一、」「二、」。留下文那次，顺序才是格式章本身。
        if replacing:
            old_i = enum_at[prefix]
            if 0 <= old_i < len(items) and items[old_i] is not None:
                seen.discard(_outline_key(items[old_i].title))
                dropped.add(old_i)
        if key in seen:
            continue
        seen.add(key)
        raw_title = raw_line.strip()
        if len(raw_title) > 40:
            raw_title = ""
        items.append(
            OutlineItem(
                id=f"o{len(items) + 1:02d}",
                title=title,
                rawTitle=raw_title if raw_title and raw_title != title else "",
                kind=kind,
                source=source_for_kind(kind),
                required=True,
                skipped=False,
                level=_extracted_item_level(raw_line),
                body=(lettered[1] if lettered else ""),
            )
        )
        if prefix:
            enum_at[prefix] = len(items) - 1
        if len(items) >= 40:
            break
    if dropped:
        items = [item for i, item in enumerate(items) if i not in dropped and item is not None]
    letter_body = {item.id: item.body for item in items if (item.body or "").strip()}
    items = dedupe_outline_items(items)
    templates = body or _template_region(raw) or raw
    _attach_item_bodies(items, templates)
    for item in items:
        kept = letter_body.get(item.id) or ""
        if kept:
            item.body = kept
    for item in items:
        if item.kind == "quote" and looks_like_quote_form(item.body or ""):
            item.source = "copy"
    return chapter, mark_outline_chapters(items)


_BODY_MAX = 80000


def has_invite_format_chapter(chapter: str) -> bool:
    n = compact_title(chapter)
    return any(
        k in n
        for k in ("投标文件格式", "响应文件格式", "投标文件组成", "响应文件组成")
    )


def titles_from_format_text(text: str) -> list[OutlineItem]:
    """知识库格式摘录：只要目录标题，不要函/表正文。"""
    from api.services.tenders.categories import is_volume_label

    _, items = extract_outline(text)
    out: list[OutlineItem] = []
    for item in items:
        if is_volume_label(item.title):
            continue
        out.append(
            item.model_copy(
                update={
                    "id": f"kb{len(out) + 1:02d}",
                    "body": "",
                    "inviteExcerpt": "",
                    "draftBody": "",
                    "source": "generate",
                }
            )
        )
        if len(out) >= 16:
            break
    return out


def choose_layout_mode(chapter: str, items: list[OutlineItem]) -> str:
    """识别到格式章就按本标大纲组卷，不再退回公司固定五件套。"""
    from api.services.tenders.slice_rules import is_format_start

    if is_format_start(chapter):
        return "outline"
    active = [item for item in items if not item.skipped]
    if not active:
        return "chapter5"
    ch = compact_title(chapter)
    if any(k in ch for k in ("投标文件格式", "响应文件格式", "投标文件组成", "响应文件组成")):
        return "outline"
    copy_kinds = {"commitment_copy", "company", "biz_dev", "unknown"}
    copy_n = sum(1 for item in active if item.kind in copy_kinds)
    if copy_n >= 1:
        return "outline"
    if any(item.kind == "biz_dev" for item in active) and any(item.kind == "tech_dev" for item in active):
        return "outline"
    if len(active) >= 3:
        return "outline"
    return "chapter5"


def looks_like_form_template(text: str) -> bool:
    """空白稿：有附件标题/下划线填空/落款。PDF 常抽成一两行，不能只按行数判断。"""
    raw = (text or "").strip()
    if len(raw) < 60:
        return False
    if _looks_like_ocr_garbage(raw):
        return False
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    markers = 0
    if re.search(r"[＿_]{3,}|_{3,}|[—－]{3,}", raw):
        markers += 1
    if _ATTACH_HINT.search(raw):
        markers += 1
    if re.search(r"(投标人|供应商).{0,8}(章|公章|签字)", raw):
        markers += 1
    if re.search(r"年\s*月\s*日|\d{4}\s*年\s*\d{1,2}\s*月", raw):
        markers += 1
    if re.search(r"致[：:]|有限公司[：:]", raw):
        markers += 1
    if re.search(r"（招标人名称）|（采购人名称）|（项目名称）", raw):
        markers += 1
    if re.search(r"(盖单位章|盖单位公章|盖公章)", raw):
        markers += 1
    if markers < 2:
        return False
    if len(lines) >= 4:
        return True
    return len(raw) >= 80 and markers >= 3


def looks_like_bid_copy_body(text: str) -> bool:
    """只有空白稿或邀请书表格才抄；须知 OCR 不当模板。"""
    raw = (text or "").strip()
    if not raw or _looks_like_ocr_garbage(raw):
        return False
    if looks_like_form_template(raw) or looks_like_quote_form(raw):
        return True
    return raw.count("|") >= 4


def can_copy_invite_body(kind: str, text: str) -> bool:
    """有模块的页只抄空白稿/表；承诺函/印鉴表/功能需求清单非 OCR 垃圾就按邀请书排。"""
    raw = (text or "").strip()
    if not raw or _looks_like_ocr_garbage(raw):
        return False
    if looks_like_bid_copy_body(raw):
        return True
    k = (kind or "").strip()
    if k in {"commitment_copy", "company", "unknown"}:
        return True
    if k in {"letter", "auth"} and re.search(
        r"（招标人名称）|（采购人名称）|（项目名称）|有限公司[：:]|（[一二三四五六七八九十]）投标函",
        raw,
    ):
        return True
    return False


def _looks_like_ocr_garbage(text: str) -> bool:
    raw = text or ""
    if len(re.findall(r"-\d{2,4}-", raw)) >= 2:
        return True
    n = compact_title(raw)
    return bool(re.search(r"\.html|操作手册", n, re.I))


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
    price_cn: str = "",
    price_en: str = "",
    extra_note: str = "",
    website: str = "",
    fax: str = "",
    postcode: str = "",
    email: str = "",
    works: bool = False,
    safety_fee: str = "",
    provisional: str = "",
    prime_cost: str = "",
    bid_bond: str = "",
    pm_name: str = "",
    pm_cert: str = "",
    pm_grade: str = "",
    defect_period: str = "",
    perf_bond: str = "",
) -> str:
    """只填单位/项目/日期/联系空位，不改承诺条款。"""
    out = unfold_form_sign_lines(text or "")
    bidder = (bidder or "").strip()
    project = (project or "").strip()
    tenderer = (tenderer or "").strip()
    legal = (legal or "").strip()
    phone = (phone or "").strip()
    address = (address or "").strip()
    contact = (contact or "").strip()
    quality = (quality or "").strip()
    extra_note = (extra_note or "").strip() or "无"
    website = (website or "").strip()
    fax = (fax or "").strip()
    postcode = (postcode or "").strip()
    email = (email or "").strip()
    date_cn = _date_cn(bid_date)
    out = re.sub(r"。?计划开工日期以招标人通知为准。?", "。", out)
    out = out.replace("【无】", "")
    out = out.replace("无其他补充说明", "无")
    out = re.sub(
        r"投标文件的上述组成部分如存在内容不一致的，以投标函为准。?",
        "",
        out,
    )
    out = out.replace("。。", "。")
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
        out = re.sub(
            r"^(?:[＿_—\-\s]{2,})?(有限公司|股份有限公司)[：:]\s*$",
            f"{tenderer}：",
            out,
            flags=re.M,
        )

        def _fill_zhi(m: re.Match[str]) -> str:
            raw_rest = (m.group(1) or "").strip()
            co, body = split_zhi_company_body(raw_rest)
            if body:
                return f"致：{co}\n{body}"
            rest = re.sub(r"[＿_—\-－\s　]+", "", raw_rest)
            if rest and _ZHI_BODY.search(rest):
                return f"致：{tenderer}\n{raw_rest}"
            return f"致：{rest or tenderer}"

        out = re.sub(r"^致[：:]\s*(.*)$", _fill_zhi, out, flags=re.M)

        def _split_mashed_zhi(m: re.Match[str]) -> str:
            rest = (m.group(2) or "")[1:].lstrip("：:")
            rest = re.sub(r"[＿_—\-－\s　]+", "", rest)
            if rest and _ZHI_BODY.search(rest):
                return f"{m.group(1)}\n致：{tenderer}\n{rest}"
            return f"{m.group(1)}\n致：{rest or tenderer}"

        out = _MASHED_ZHI.sub(_split_mashed_zhi, out)
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

        def _fill_bidder(m: re.Match[str]) -> str:
            val = re.sub(r"[＿_—\-－\s　]+", "", m.group(2) or "")
            suf = m.group(3) or ""
            if "签字" in suf:
                return m.group(0)
            return f"{m.group(1)}：{val or bidder}{suf}"

        out = re.sub(
            r"^(投标人|供应商)\s*[（(](盖单位公章|盖公章|公章|盖章|章)[)）][：:]?\s*$",
            rf"\1：{bidder}（\2）",
            out,
            flags=re.M,
        )
        out = re.sub(
            r"^(投标人|供应商)[：:]\s*([^（(\n]{0,40}?)(\s*[（(][^)）]*[)）])?\s*$",
            _fill_bidder,
            out,
            flags=re.M,
        )
    # 「签字」栏留给本人手签，不填姓名。
    if contact:
        out = re.sub(r"(联系人)[：:]\s*[＿_—\-\s]{2,}", rf"\1：{contact}", out)
        out = re.sub(r"^(联系人)[：:]*\s*$", rf"\1：{contact}", out, flags=re.M)
    if phone:
        out = re.sub(r"(联系电话|电\s*话)[：:]\s*[＿_—\-\s]{2,}", rf"\1：{phone}", out)
        out = re.sub(r"^(联系电话|电话)[：:]*\s*$", rf"\1：{phone}", out, flags=re.M)
    if address:
        out = re.sub(r"(地址|住址)[：:]\s*[＿_—\-\s]{2,}", rf"\1：{address}", out)
    if quality:
        out = re.sub(r"(质量要求|质量标准)[：:]\s*[＿_—\-]{2,}", rf"\1：{quality}", out)
        out = re.sub(
            r"(质量要求|质量标准)[：:]\s*(?:满足招标文件(?:要求)?)?\s*$",
            rf"\1：{quality}",
            out,
            flags=re.M,
        )
    if delivery_days is not None:
        days = str(int(delivery_days))
        out = re.sub(
            r"(通知[^。\n]{0,12}起)[＿_—\-]{1,12}(天)",
            rf"\g<1>{days}\2",
            out,
        )
        out = re.sub(
            r"(计划工期|工期)[：:；;]\s*[＿_—\-－]*\s*日历天",
            rf"\1：{days}日历天",
            out,
        )
        out = re.sub(
            r"(计划工期|施工工期|工期)[：:]\s*[＿_—\-－]*\s*$",
            rf"\1：{days}日历天",
            out,
            flags=re.M,
        )
        out = re.sub(
            r"交货期[：:；;]?\s*[＿_—\-－]*\s*(?:天内|日历天)",
            f"交货期：{days}日历天",
            out,
        )
    if extra_note:
        out = re.sub(
            r"((?:其他)?补充说明)[：:）)\s＿_—\-－（。]*$",
            rf"\1：{extra_note}",
            out,
            flags=re.M,
        )
        out = re.sub(
            r"(补充说明[^\n]*（[^）\n]*)$",
            r"\1）",
            out,
            flags=re.M,
        )
    if "补遗书" in out:
        out = re.sub(r"（含补遗书[^）\n]{0,40}）", "（含补遗书无）", out)
        out = re.sub(r"（含补遗书无(?!）)", "（含补遗书无）", out)
    for lab, val in (
        ("安全生产措施费|安全文明施工费", (safety_fee or "").strip()),
        ("暂列金额", (provisional or "").strip()),
        ("专业工程暂估价", (prime_cost or "").strip()),
        ("投标保证金", (bid_bond or "").strip()),
        ("缺陷责任期", (defect_period or "").strip()),
        ("履约担保金额|履约担保", (perf_bond or "").strip()),
    ):
        if not val:
            continue
        out = re.sub(
            rf"({lab})[：:]\s*(?:无|[＿_—\-－无【】大写小写（）()\s]*)\s*$",
            rf"\1：{val}",
            out,
            flags=re.M,
        )
        out = re.sub(
            rf"({lab})(\s*[|｜]\s*)(?:无|[＿_—\-－\s]*)\s*$",
            rf"\1\2{val}",
            out,
            flags=re.M,
        )
    bond = (bid_bond or "").strip()
    if bond:
        pair = re.match(r"^(.+?)（¥([0-9.]+)元）$", bond)
        if pair:
            out = re.sub(
                r"(投标保证金[^\n]{0,40}人民币（大写）[：:]?)[＿_—\-－\s　]*",
                rf"\g<1>{pair.group(1)}",
                out,
                count=1,
            )
            out = re.sub(
                r"(投标保证金[^\n]{0,80}小写[：:]?[￥¥]?)[＿_—\-－\s　]*(?=元)",
                rf"\g<1>{pair.group(2)}",
                out,
                count=1,
            )
    pm_name = _clean_pm_name(pm_name)
    pm_cert = (pm_cert or "").strip()
    if pm_cert in {"/", "／", "无"}:
        pm_cert = ""
    grade = (pm_grade or "").strip()
    if grade in {"/", "／"}:
        grade = "无"
    out = re.sub(
        r"(项目经理(?:姓名)?)[：:][ \t]*(.+)$",
        lambda m: f"{m.group(1)}：{pm_name or '________'}"
        if not _clean_pm_name(m.group(2))
        else (f"{m.group(1)}：{pm_name}" if pm_name else m.group(0)),
        out,
        flags=re.M,
    )
    if pm_name:
        out = re.sub(
            r"(项目经理(?:姓名)?)[：:][ \t]*[＿_—\-－/／]*[ \t]*$",
            rf"\1：{pm_name}",
            out,
            flags=re.M,
        )
    else:
        out = re.sub(
            r"(项目经理(?:姓名)?)[：:][ \t]*[/／无]?[ \t]*$",
            r"\1：________",
            out,
            flags=re.M,
        )
    if pm_cert:
        out = re.sub(
            r"(注册证号|证书编号|执业印章号)[：:][ \t]*[＿_—\-－/／]*[ \t]*$",
            rf"\1：{pm_cert}",
            out,
            flags=re.M,
        )
    else:
        out = re.sub(
            r"(注册证号|证书编号|执业印章号)[：:][ \t]*[/／无]?[ \t]*$",
            r"\1：________",
            out,
            flags=re.M,
        )
    out = re.sub(
        r"(专业(?:等级)?|注册专业)[：:][ \t]*[/／＿_—\-－]*[ \t]*$",
        rf"\1：{grade or '无'}",
        out,
        flags=re.M,
    )
    for lab, val in (
        ("网\\s*址", website),
        ("传\\s*真", fax),
        ("邮政编码", postcode),
        ("邮\\s*编", postcode),
        ("电子邮箱|电子信箱|邮\\s*箱", email),
    ):
        if not (val or "").strip():
            continue
        out = re.sub(
            rf"({lab})[：:]\s*[＿_—\-－\s]*$",
            lambda m, v=val: f"{m.group(1)}：{v}",
            out,
            flags=re.M,
        )
    if price_cn:
        out = re.sub(
            r"(人民币[（(]大写[)）])(?:[\s＿_—\-－]*)(?=[（(]¥|元)",
            rf"\1{price_cn}",
            out,
        )
    if price_en:
        fig = price_en.replace(",", "")
        out = re.sub(
            r"（[¥￥](?:[\s＿_—\-－]*)(?=元|\n)",
            f"（¥{fig}",
            out,
            count=1,
        )
    out = re.sub(
        r"[¥￥]\s*([\d,]+(?:\.\d+)?)",
        lambda m: f"¥{m.group(1).replace(',', '')}",
        out,
    )
    if validity_days is not None:
        days = str(int(validity_days))
        out = re.sub(
            r"(开标之日起|投标之日起|截止之日起)[＿_—\-]{1,12}(天)",
            rf"\g<1>{days}\2",
            out,
        )
        out = re.sub(
            r"(投标有效期)[：:][^\n]*",
            rf"\1：自投标截止之日起{days}日历天",
            out,
        )
    if date_cn:
        out = re.sub(r"[＿_—\-]{0,6}年[＿_—\-]{0,4}月[＿_—\-]{0,4}日", date_cn, out)
        # 含「年 月 日四、…」粘连：先填日期，再拆行（勿用负向断言挡住）
        out = re.sub(r"年\s*月\s*日", date_cn, out)
        out = re.sub(
            rf"({re.escape(date_cn)})\s*([一二三四五六七八九十]、\s*"
            r"(?:合同|偏差|偏离|报价|响应方案|响应书|承诺|授权|身份))",
            r"\1\n\2",
            out,
        )
        out = re.sub(
            rf"({re.escape(date_cn)})\s*((?:供应商|投标人)\s*[：:].{{0,8}}参见)",
            r"\1\n\2",
            out,
        )
    return out


def _clean_pm_name(raw: str) -> str:
    t = (raw or "").strip()
    t = re.sub(r"[＿_—\-－/\s／]+", "", t)
    if t in {"", "无", "暂无", "待定", "空白", "姓名"}:
        return ""
    if any(
        k in t
        for k in (
            "是指",
            "由承",
            "承包",
            "拟派",
            "拟任",
            "详见",
            "如下",
            "项目",
            "经理",
            "投标",
            "招标",
            "应当",
            "必须",
            "填写",
            "注册",
            "证书",
        )
    ):
        return ""
    if re.fullmatch(r"[\u4e00-\u9fff]{2,4}", t):
        return t
    return ""


def _insert_before_sign(text: str, sentence: str) -> str:
    raw = text or ""
    if not sentence or sentence in raw:
        return raw
    hit = re.search(
        r"^(?:投标人|供应商).{0,24}(?:盖单位公章|盖单位章|盖章)|^(?:法定代表人)",
        raw,
        flags=re.M,
    )
    if hit:
        return raw[: hit.start()].rstrip() + "\n" + sentence + "\n" + raw[hit.start() :]
    return raw.rstrip() + "\n" + sentence + "\n"


def ensure_works_letter_fixed(text: str, *, quality: str = "", valid_days: int | None = None, bid_bond: str = "") -> str:
    """施工投标函缺固定句时补上，不删原文。"""
    out = text or ""
    blob = re.sub(r"\s+", "", out)
    if "我方已仔细研究" not in blob and "愿意以人民币" not in blob:
        return out
    if "投标函附录属于合同文件的组成部分" not in blob:
        out = _insert_before_sign(out, "随同本投标函递交的投标函附录属于合同文件的组成部分。")
    if "投标保证金" not in blob:
        if bid_bond:
            line = f"随同本投标函提交投标保证金一份，金额人民币（大写）：{bid_bond}。"
        else:
            line = "随同本投标函提交投标保证金一份，金额人民币（大写）：________元（小写￥________元）。"
        out = _insert_before_sign(out, line)
    q = (quality or "").strip()
    if q and "修补工程" not in re.sub(r"\s+", "", out) and "工程质量达到" not in re.sub(r"\s+", "", out):
        if re.search(r"质量要求[：:]", out):
            out = re.sub(r"(质量要求[：:][^\n]*)", rf"\1；修补工程中的任何缺陷，工程质量达到：{q}。", out, count=1)
        else:
            out = _insert_before_sign(
                out,
                f"按合同约定实施和完成承包工程，修补工程中的任何缺陷，工程质量达到：{q}。",
            )
    if valid_days is not None and "自投标截止之日起" not in re.sub(r"\s+", "", out):
        days = str(int(valid_days))
        if re.search(r"投标有效期", out):
            out = re.sub(
                r"投标有效期[：:][^\n]*",
                f"投标有效期：自投标截止之日起{days}日历天。",
                out,
                count=1,
            )
        else:
            out = _insert_before_sign(out, f"我方承诺投标有效期为自投标截止之日起{days}日历天。")
    return out


def ensure_appendix_fields(
    text: str,
    *,
    project: str = "",
    bidder: str = "",
    scope: str = "",
    price: str = "",
    safety_fee: str = "",
    provisional: str = "",
    prime_cost: str = "",
    pm_name: str = "",
    pm_cert: str = "",
    valid: str = "",
    defect: str = "",
    perf_bond: str = "",
    grade: str = "",
) -> str:
    out = text or ""
    blob = re.sub(r"\s+", "", out)
    rows = (
        ("项目名称", (project or "").strip() or "________"),
        ("投标人", (bidder or "").strip() or "________"),
        ("投标范围", (scope or "").strip() or "________"),
        ("投标总报价", (price or "").strip() or "________"),
        ("安全生产措施费", (safety_fee or "").strip() or "________"),
        ("暂列金额", (provisional or "").strip() or "________"),
        ("专业工程暂估价", (prime_cost or "").strip() or "________"),
        ("项目经理", _clean_pm_name(pm_name) or "________"),
        ("注册证号", (pm_cert or "").strip() or "________"),
        ("投标有效期", (valid or "").strip() or "________"),
        ("缺陷责任期", (defect or "").strip() or "________"),
        ("履约担保金额", (perf_bond or "").strip() or "________"),
        ("专业等级", (grade or "").strip() or "无"),
    )
    for lab, val in rows:
        if lab in blob:
            continue
        out = _insert_before_sign(out, f"{lab}：{val}")
        blob = re.sub(r"\s+", "", out)
    out = re.sub(
        r"(专业(?:等级)?|注册专业)[：:]\s*[/／]\s*$",
        rf"\1：{(grade or '').strip() or '无'}",
        out,
        flags=re.M,
    )
    out = re.sub(
        r"(项目经理(?:姓名)?)[：:][ \t]*(.+)$",
        lambda m: f"{m.group(1)}：{_clean_pm_name(pm_name) or '________'}"
        if not _clean_pm_name(m.group(2))
        else m.group(0),
        out,
        flags=re.M,
    )
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
        end = min(end, _next_attach_cut(chapter_body, start, item.title))
        chunk = chapter_body[start:end]
        item.body = _clean_copied_body(chunk, item.title)
        kind = (item.kind or "").strip()
        if kind == "letter":
            item.body = slice_copied_letter_body(item.body, item.title)
        elif kind == "commitment_copy" or "响应书" in compact_title(item.title):
            item.body = _trim_copied_form_tail(item.body, item.title)
        elif kind in {"biz_dev", "tech_dev"}:
            # 模块表只用表头；正文里落款/下一节别跟着污染
            item.body = _trim_copied_form_tail(item.body, item.title)


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
    all_pos = sorted(p for hits in found for p in hits)
    starts: list[int] = []
    for hits in found:
        starts.append(_pick_heading_start(hits, all_pos, body))
    return starts


def _toc_leader_line(body: str, pos: int) -> bool:
    nl = body.find("\n", pos)
    line = body[pos : nl if nl >= 0 else len(body)]
    return bool(re.search(r"[.．…]{3,}|·{3,}", line))


def _pick_heading_start(hits: list[int], all_pos: list[int], body: str) -> int:
    """目录标题下一行就是下一条。取正文里第一次拉开距离的，点线目录和文末附表不算。"""
    if not hits:
        return -1
    real = [p for p in hits if not _toc_leader_line(body, p)] or list(hits)

    def after(pos: int) -> int:
        nl = body.find("\n", pos)
        return nl + 1 if nl >= 0 else len(body)

    for pos in real:
        later = [p for p in all_pos if p > pos]
        end = later[0] if later else len(body)
        if end - after(pos) >= 30:
            return pos
    return real[-1]


_NEXT_FORM_SEC = re.compile(
    r"[一二三四五六七八九十]{1,2}、\s*"
    r"(?:合同条款|商务和技术|商务|技术)?(?:偏差|偏离|报价|响应方案|响应书|承诺|授权|身份)"
)


def _next_attach_cut(body: str, start: int, title: str) -> int:
    seq = _attach_seq(title)
    nl = body.find("\n", start)
    skip = (nl - start + 1) if 0 <= nl < start + 80 else min(24, max(4, len(title or "")))
    tail = body[start + skip :]
    cut = len(body)
    pack_at = _pack_cut_pos(tail, title)
    if pack_at > 0:
        cut = start + skip + pack_at
    for m in re.finditer(
        r"(?:附(?:件)?[（(]?[一二三四五六七八九十0-9]+"
        r"|(?:^|\n)[一二三四五六七八九十]{1,2}、\s*(?:合同|偏差|偏离|报价|响应方案|响应书|承诺|授权|身份))",
        tail,
    ):
        other = m.group(0)
        if seq and _attach_seq(other) == seq:
            continue
        if compact_title(other) and _attach_seq(other) == seq:
            continue
        pos = m.start()
        if other.startswith("\n"):
            pos += 1
        cut = min(cut, start + skip + pos)
        break
    # OCR 无换行时「…日四、响应方案」也要截断
    for m in _NEXT_FORM_SEC.finditer(tail):
        other = m.group(0)
        if seq and _attach_seq(other) == seq:
            continue
        t_core = compact_title(title or "")
        o_core = compact_title(other)
        if t_core and o_core and (t_core in o_core or o_core in t_core):
            continue
        cut = min(cut, start + skip + m.start())
        break
    return cut


def _trim_copied_form_tail(text: str, title: str = "") -> str:
    """响应书/抄写稿：落款后下一节标题（含粘连）一律丢掉。"""
    raw = (text or "").rstrip()
    if not raw:
        return ""
    cut = len(raw)
    title_n = compact_title(title or "")
    for m in _NEXT_FORM_SEC.finditer(raw):
        if m.start() < 12:
            continue
        other = compact_title(m.group(0))
        if title_n and other and (title_n in other or other in title_n):
            continue
        # 本页标题行里的「三、合同条款响应书」不砍
        if title_n and "响应书" in title_n and "响应书" in other and m.start() < 40:
            continue
        cut = min(cut, m.start())
        break
    return raw[:cut].rstrip()


def _core_doc_name(text: str) -> str:
    """去掉附件序号、括号后的材料名，便于目录「投标函（附件一）」对上正文「投 标 函」。"""
    n = compact_title(text)
    n = _ATTACH_HINT.sub("", n)
    n = re.sub(r"^附(?:件)?", "", n)
    n = re.sub(r"[（）()：:、.．]", "", n)
    n = _DOC_TAIL.sub("", n)
    return n


def _attach_seq(text: str) -> str:
    match = _ATTACH_SEQ.search(compact_title(text) or text or "")
    return match.group(1) if match else ""


def _is_body_heading(line: str, title: str) -> bool:
    raw = (line or "").strip()
    if raw.endswith("；") or raw.endswith(";"):
        return False
    if re.match(r"^[0-9]{1,2}、", raw) and "附件" in raw:
        return False
    peeled = _ITEM_PREFIX.sub("", raw).strip(" :：.．、")
    n = compact_title(line)
    t = compact_title(title)
    peeled_n = compact_title(peeled)
    # 「二、法定代表人身份证明投标人名称：」标题和表栏粘在一行，仍是这一节开头
    if t and peeled_n.startswith(t):
        extra = peeled_n[len(t) :]
        if not extra.startswith(("及", "和", "或", "附录", "格式", "组成")):
            return True
    if not t or not n:
        return False
    if len(n) > 48:
        if len(t) >= 4 and t in n and (_ATTACH_HINT.search(n) or n.startswith(t)):
            return True
        return False
    if _SENTENCE.search(n) or _CLAUSE_START.search(n):
        return False
    rest = _ATTACH_HINT.sub("", n)
    rest = re.sub(r"[.．…·]+[0-9]{1,4}$", "", rest)
    rest = re.sub(r"[（）()：:、.．]", "", rest)
    if rest == t or n == t:
        return True
    if rest.startswith(t) and len(rest) - len(t) <= 16:
        return True
    if n.startswith(t) and len(n) - len(t) <= 16:
        return True
    stem = re.sub(r"[表书函]$", "", t)
    if len(stem) >= 4 and (n.startswith(stem) or rest.startswith(stem)):
        return True
    # 目录写「施工组织格式」、正文标题是「施工组织设计」
    t_bare = re.sub(r"(?:格式|空白稿|模板)$", "", t)
    n_bare = re.sub(r"(?:格式|设计|空白稿|模板)$", "", n)
    if len(t_bare) >= 4 and t_bare == n_bare:
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


def _is_ocr_junk_line(line: str) -> bool:
    s = (line or "").strip()
    if not s:
        return False
    if _PAGE_MARK.match(s):
        return True
    n = compact_title(s)
    if _COMPANY_ONLY.match(n) and not re.search(r"(投标|承诺|授权|委托|法人)", n):
        return True
    return False


def drop_ocr_junk_lines(lines: list[str]) -> list[str]:
    return [ln for ln in lines if not _is_ocr_junk_line(ln)]


def split_zhi_company_body(rest: str) -> tuple[str, str]:
    compact = re.sub(r"[＿_—\-－\s　]+", "", rest or "")
    m = _CO_THEN_BODY.match(compact)
    if m:
        return m.group("co"), m.group("body")
    return compact, ""


def split_mashed_zhi_line(line: str) -> list[str]:
    n = compact_title(line)
    m = _MASHED_ZHI.match(n)
    if not m:
        extra = compact_title(line)
        if extra.startswith("致"):
            rest = extra[1:].lstrip("：:")
            co, body = split_zhi_company_body(rest)
            if body:
                return [f"致：{co}", body]
            if _ZHI_BODY.search(extra):
                return ["致：", rest]
        return [line]
    rest = m.group(2)
    if rest in {"致", "致：", "致:"}:
        extra = "致："
    elif rest.startswith("致：") or rest.startswith("致:"):
        extra = "致：" + rest[2:].lstrip("：:")
    else:
        extra = "致：" + rest[1:].lstrip("：:")
    if extra != "致：":
        co, body = split_zhi_company_body(extra[2:].lstrip("：:"))
        if body:
            return [m.group(1), f"致：{co}", body]
        if _ZHI_BODY.search(extra):
            return [m.group(1), "致：", extra[2:].lstrip("：:")]
    return [m.group(1), extra]


def _peel_trailing_date(chunk: str) -> list[str]:
    s = (chunk or "").strip()
    if not s or s.startswith("日期"):
        return [s] if s else []
    m = re.search(r"(\d{4}年\d{1,2}月\d{1,2}日)$", s)
    if m and re.match(r"(联系人|联系电话|电话)", s):
        head = s[: m.start()].rstrip("：:") + "："
        return [head, f"日期：{m.group(1)}"]
    return [s]


def unfold_form_sign_lines(text: str) -> str:
    """OCR 常把落款挤在一行，按标签拆开再填空。表格行不要拆。"""
    out: list[str] = []
    for ln in (text or "").splitlines():
        raw = ln.strip()
        if not raw:
            continue
        if "|" in raw:
            out.append(raw)
            continue
        hits = list(_SIGN_HEAD.finditer(raw))
        if len(hits) < 2:
            out.extend(_peel_trailing_date(raw))
            continue
        prefix = raw[: hits[0].start()].strip()
        if prefix:
            out.append(prefix)
        for i, h in enumerate(hits):
            end = hits[i + 1].start() if i + 1 < len(hits) else len(raw)
            chunk = raw[h.start() : end].strip()
            if chunk:
                out.extend(_peel_trailing_date(chunk))
    return "\n".join(out)


def _unmash_title_line(lines: list[str], title: str) -> list[str]:
    if not lines:
        return lines
    out: list[str] = []
    t = compact_title(title) if title else ""
    for ln in lines:
        split = split_mashed_zhi_line(ln)
        if len(split) > 1:
            head = split[0]
            out.append(title if t and compact_title(head) == t else head)
            out.extend(split[1:])
            continue
        n = compact_title(ln)
        if t and n.startswith(t) and n != t:
            extra = n[len(t) :].lstrip("：:、，,")
            out.append(title)
            if extra:
                out.append(extra)
            continue
        out.append(ln)
    return out


def _clean_copied_body(chunk: str, title: str) -> str:
    text = (chunk or "").strip()
    if not text:
        return ""
    lines = text.splitlines()
    while lines and _is_skipped_form_prefix(lines[0]):
        lines = lines[1:]
    lines = _unmash_title_line(lines, title)
    lines = drop_ocr_junk_lines(lines)
    text = "\n".join(lines).strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    if len(text) < 12:
        return ""
    compact = compact_title(text)
    if "\n" not in text and len(compact) <= 28 and classify_kind(text) != "unknown":
        return ""
    text = cut_invite_pack_body(text, title)[:_BODY_MAX]
    if "响应书" in compact_title(title or "") or classify_kind(title or "") == "commitment_copy":
        text = _trim_copied_form_tail(text, title)
    return text


def _format_heading_match(text: str) -> re.Match | None:
    """跳过「按照/详见响应文件格式」这种句子，取格式章标题。"""
    matches = list(_FORMAT_HEAD.finditer(text or ""))
    if not matches:
        return None
    good: list[re.Match] = []
    for match in matches:
        prev = (text or "")[max(0, match.start() - 8) : match.start()]
        if any(k in prev for k in ("按照", "详见", "参见", "规定", "满足")):
            continue
        around = (text or "")[max(0, match.start() - 2) : match.end() + 1]
        if any(q in around for q in ("“", "”", "\"", "「", "」")):
            continue
        good.append(match)
    pool = good or matches
    # 「响应文件格式」才是要照抄的空白稿；「响应文件的组成」多半是前面的材料清单。
    fmt = [m for m in pool if "格式" in (m.group("title") or "")]
    if fmt:
        pool = fmt
    headed = [m for m in pool if compact_title(m.group("title") or "").startswith("第")]
    if headed:
        pool = headed
    # 目录和正文都会出现格式章。正文章通常更靠后，但页眉重复时后面没有目录。
    # 取后面仍能抽出目录行的最后一处。
    best = pool[0]
    best_n = -1
    for match in pool:
        _title, body = _slice_format(text, match)
        n = 0
        for line in body.splitlines()[:60]:
            s = line.strip()
            if not s:
                continue
            if _ITEM_PREFIX.match(s) or _ATTACH_HINT.search(s) or lettered_heading(s):
                n += 1
        if n >= best_n:
            best = match
            best_n = n
    return best


def _format_section(text: str) -> tuple[str, str]:
    match = _format_heading_match(text or "")
    if match is None:
        return "", ""
    return _slice_format(text, match)


def _template_region(text: str) -> str:
    """目录里也会写「第X部分投标文件格式」，空白稿以最后一次为准。"""
    matches = list(_FORMAT_HEAD.finditer(text or ""))
    if matches:
        _title, body = _slice_format(text, matches[-1])
        return body
    last_pos: dict[str, int] = {}
    for m in re.finditer(
        r"(?:^|\n)(?P<head>附(?:件)?[（(]?(?P<seq>[一二三四五六七八九十0-9]+)[)）]?[：:、．.])",
        text or "",
    ):
        last_pos[m.group("seq")] = m.start("head")
    if last_pos:
        return (text or "")[min(last_pos.values()) :]
    return ""


def _slice_format(text: str, match: re.Match) -> tuple[str, str]:
    title = re.sub(r"\s+", "", match.group("title") or "").strip()
    rest = text[match.end() :]
    start_num = ""
    head_line = _CHAPTER_LINE.match(title)
    if head_line:
        start_num = head_line.group("num") or ""
    if not start_num:
        return title, rest[:200000]
    stop = len(rest)
    for line in rest.splitlines():
        stripped = line.strip()
        ch = _CHAPTER_LINE.match(stripped)
        if not ch:
            continue
        num = ch.group("num") or ""
        rest_title = ch.group("rest") or ""
        if num == start_num:
            continue
        if any(k in rest_title for k in ("格式", "组成", "邀请", "须知", "说明")):
            continue
        idx = rest.find(line)
        if idx >= 0:
            stop = idx
            break
    return title, rest[:stop]


def _candidate_lines(body: str, *, whole_doc: bool) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for raw in (body or "").splitlines():
        for piece in raw.split("|"):
            if len(list(_GLUE_ENUM.finditer(piece))) >= 2:
                continue
            lettered = lettered_heading(piece)
            if lettered:
                title, _body = lettered
                if title and not is_noise_title(title):
                    out.append((title, piece.strip()))
                continue
            title = _clean_item(piece)
            if not title or is_noise_title(title):
                continue
            pack = _format_pack_heading(title)
            if not pack and not is_chapter_title(title):
                continue
            if not pack and not _is_catalog_item(piece, title, whole_doc=whole_doc):
                continue
            out.append((title, piece.strip()))
    return out


def _is_catalog_item(raw: str, title: str, *, whole_doc: bool) -> bool:
    """材料名或「材料名（附件N）」才收；公司名+附件N 的页眉不收。"""
    piece = (raw or "").strip()
    n = compact_title(title)
    known = classify_kind(title) != "unknown"
    attach = bool(_ATTACH_HINT.search(piece) or _ATTACH_HINT.search(title))
    prefixed = bool(_ITEM_PREFIX.match(piece))
    doc_like = bool(_DOC_NOUN.search(n))
    if _format_pack_heading(title):
        return True
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
    if _FORM_TITLE_TAIL.search(n) and classify_kind(_FORM_TITLE_TAIL.sub("", n)) != "unknown":
        return True
    return False


def _is_doc_title_len(text: str) -> bool:
    n = text or ""
    if "功能需求" in n:
        return True
    return n.endswith(
        ("函", "表", "清单", "书", "证明", "简历", "执照", "保函", "方案", "承诺", "保证金", "业绩", "模板")
    )


def _clean_item(raw: str) -> str:
    text = (raw or "").strip().strip("·•-—_ ")
    text = _PAGE_TAIL.sub("", text).strip()
    text = re.sub(r"^\d+(?:\.\d+)+[、.．:：\s]*", "", text)
    text = _ITEM_PREFIX.sub("", text).strip(" :：.．、；;。")
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"致$", "", text)
    text = _peel_form_suffix(text)
    if not text or _SKIP_SUB.search(text):
        return ""
    if len(text) < 2:
        return ""
    cap = 24 if _is_doc_title_len(text) else 22
    if len(text) > cap:
        return ""
    if text.isdigit():
        return ""
    return text
