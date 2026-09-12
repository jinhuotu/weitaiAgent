"""投标文件填空字段。邀请书可由 LLM 抽项目侧字段；投标人侧由表单/默认值提供。"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field


class PlaceholderItem(BaseModel):
    """Word 中待补扫描件的方框。"""

    model_config = ConfigDict(populate_by_name=True)

    key: str = ""
    title: str
    hint: str = ""


class QuoteLineIn(BaseModel):
    """分项报价一行。由邀请书/工程量清单解析或页面改写。"""

    model_config = ConfigDict(populate_by_name=True)

    seq: str = ""
    name: str = ""
    spec: str = ""
    unit: str = ""
    qty: float = 0
    unitPrice: float = Field(default=0, ge=0)
    amount: float = Field(default=0, ge=0)
    groups: list[str] = Field(default_factory=list)


class DeviationLine(BaseModel):
    """技术偏差表一行。"""

    model_config = ConfigDict(populate_by_name=True)

    seq: str = ""
    requirement: str = ""
    response: str = ""
    deviation: str = "无偏差"


class PerformanceLine(BaseModel):
    """企业业绩表一条（对应第五章类似项目/在建项目）。"""

    model_config = ConfigDict(populate_by_name=True)

    projectName: str = ""
    spec: str = ""
    location: str = ""
    client: str = ""
    contact: str = ""
    amountYuan: float = Field(default=0, ge=0)
    summary: str = ""
    note: str = ""
    ongoing: bool = False
    chargerRelated: bool = False
    includeInBid: bool = True


class PerformanceRequirement(BaseModel):
    """本标招标书对类似业绩的资格门槛。没有抽出则为空，不套用充电桩默认 20 万。"""

    model_config = ConfigDict(populate_by_name=True)

    similarScope: str = ""
    keywords: list[str] = Field(default_factory=list)
    minAmountYuan: float = Field(default=0, ge=0)
    minCount: int = Field(default=0, ge=0, le=20)
    requireCompleted: bool = False
    note: str = ""


OUTLINE_KINDS: tuple[str, ...] = (
    "letter",
    "legal_id",
    "auth",
    "quote",
    "biz_dev",
    "tech_dev",
    "commitment_copy",
    "scan",
    "performance",
    "factory",
    "tech_plan",
    "company",
    "unknown",
)

OUTLINE_SOURCES: tuple[str, ...] = ("generate", "copy", "skip")

PAGE_NUMBER_POS: tuple[str, ...] = ("bottom-center", "bottom-right", "none")
PAGE_NUMBER_START: tuple[str, ...] = ("toc", "body", "cover")
TOC_NUMBERING: tuple[str, ...] = ("cn", "arabic", "paren", "attach")


class DocumentFormat(BaseModel):
    """招标书抽出的排版要求。未写明的字段保持默认，不臆造。"""

    model_config = ConfigDict(populate_by_name=True)

    specified: bool = False
    notes: list[str] = Field(default_factory=list)

    marginLeftCm: float = Field(default=2.8, ge=1.0, le=5.0)
    marginRightCm: float = Field(default=2.6, ge=1.0, le=5.0)
    marginTopCm: float = Field(default=2.6, ge=1.0, le=5.0)
    marginBottomCm: float = Field(default=2.5, ge=1.0, le=5.0)

    fontName: str = Field(default="宋体", max_length=32)
    bodySizePt: float = Field(default=12, ge=8, le=26)
    headingSizePt: float = Field(default=16, ge=10, le=26)
    coverTitleSizePt: float = Field(default=22, ge=12, le=42)
    coverDocSizePt: float = Field(default=26, ge=14, le=42)
    tocTitleSizePt: float = Field(default=16, ge=10, le=26)
    tocItemSizePt: float = Field(default=12, ge=9, le=18)

    coverRequired: bool = True
    coverShowProject: bool = True
    coverShowTenderNo: bool = False
    coverShowBidder: bool = True
    coverShowCopyMark: bool = False
    coverCopyMark: str = Field(default="正本", max_length=8)
    coverShowDate: bool = True
    coverNeedSeal: bool = False

    tocNumbering: str = Field(default="cn", max_length=12)
    tocNeedPageNos: bool = True

    pageNumberPos: str = Field(default="bottom-center", max_length=24)
    pageNumberStart: str = Field(default="toc", max_length=12)


class OutlineItem(BaseModel):
    """招标书「投标/响应文件格式」里的一条组卷要求。"""

    model_config = ConfigDict(populate_by_name=True)

    id: str = ""
    title: str
    kind: str = "unknown"
    source: str = "copy"
    required: bool = True
    skipped: bool = False
    body: str = ""


class BidBrief(BaseModel):
    """一次生成所需的全部填空项。"""

    model_config = ConfigDict(populate_by_name=True)

    projectName: str = Field(default="")
    tenderer: str = Field(default="")
    bidContent: str = Field(default="")
    quality: str = Field(default="合格")
    deliveryDays: int = Field(default=30, ge=1, le=3650)
    warrantyYears: int = Field(default=2, ge=1, le=20)
    bidValidityDays: int = Field(default=60, ge=1, le=365)
    bidPriceYuan: float = Field(default=0, ge=0)
    prepaidPct: int = Field(default=30, ge=0, le=100)
    arrivalPct: int = Field(default=50, ge=0, le=100)
    settlementPct: int = Field(default=17, ge=0, le=100)
    warrantyPct: int = Field(default=3, ge=0, le=100)
    bidDate: str = Field(default_factory=lambda: date.today().isoformat())
    tenderNo: str = Field(default="")

    bidderName: str = Field(default="河南伟泰光电科技有限公司")
    bidderNature: str = Field(default="有限责任公司")
    bidderAddress: str = Field(
        default="河南省郑州市高新区开发区梧桐街与红松路交叉口东南角远大产业园区内6号楼三层"
    )
    bidderWebsite: str = Field(default="")
    bidderPhone: str = Field(default="17630567052")
    bidderFax: str = Field(default="")
    bidderPostcode: str = Field(default="")
    bidderEmail: str = Field(default="gzwceo@163.com")
    foundedDate: str = Field(default="2017年07月")
    businessTerm: str = Field(default="长期")

    legalPersonName: str = Field(default="郭志伟")
    legalPersonGender: str = Field(default="男")
    legalPersonAge: str = Field(default="28")
    legalPersonTitle: str = Field(default="执行董事")
    legalPersonIdNo: str = Field(default="410521199802104053")

    agentName: str = Field(default="")
    agentIdNo: str = Field(default="")
    agentAuthUntil: str = Field(default="")

    trafficFeeNote: str = Field(default="质保期外流量年收费标准：7元/年")
    extraNote: str = Field(default="")
    factoryRole: str = Field(default="")
    attachQualifications: bool = Field(default=False)
    includePlaceholders: bool = Field(default=True)
    includeCommitment: bool = Field(default=True)
    extraPlaceholders: list[PlaceholderItem] = Field(default_factory=list)
    requiredSlotKeys: list[str] = Field(default_factory=list)
    includeSlotKeys: list[str] = Field(default_factory=list)

    quoteTitle: str = Field(default="")
    quoteTaxRate: float = Field(default=0.13, ge=0, le=1)
    quoteSourceIncTax: float = Field(default=0, ge=0)
    quoteSource: str = Field(default="")
    quoteLines: list[QuoteLineIn] = Field(default_factory=list)
    quoteHeaders: list[str] = Field(default_factory=list)
    quoteRoles: list[str] = Field(default_factory=list)
    deviationLines: list[DeviationLine] = Field(default_factory=list)
    bizDevHeaders: list[str] = Field(default_factory=list)
    techDevHeaders: list[str] = Field(default_factory=list)
    performanceLines: list[PerformanceLine] = Field(default_factory=list)
    performanceRequirement: PerformanceRequirement = Field(default_factory=PerformanceRequirement)

    constructionPlan: str = Field(default="")
    layoutPlan: str = Field(default="")
    powerPlan: str = Field(default="")
    omPlan: str = Field(default="")
    schedulePlan: str = Field(default="")
    techPlanNote: str = Field(default="")

    # chapter5：公司固定投标文件格式（可更换空白稿）；outline：按本标招标书大纲组卷。
    layoutMode: str = Field(default="chapter5")
    outlineChapter: str = Field(default="")
    outlineItems: list[OutlineItem] = Field(default_factory=list)
    documentFormat: DocumentFormat = Field(default_factory=DocumentFormat)
    # 解析邀请书时写入，供生成后对照原文做 AI 质检。
    invitationId: str = Field(default="", max_length=16)


COMPANY_FIELD_KEYS: tuple[str, ...] = (
    "bidderName",
    "bidderNature",
    "bidderAddress",
    "bidderWebsite",
    "bidderPhone",
    "bidderFax",
    "bidderPostcode",
    "bidderEmail",
    "foundedDate",
    "businessTerm",
    "legalPersonName",
    "legalPersonGender",
    "legalPersonAge",
    "legalPersonTitle",
    "legalPersonIdNo",
)


def default_brief() -> BidBrief:
    return BidBrief()
