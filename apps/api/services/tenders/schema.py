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

    legalPersonName: str = Field(default="张朝文")
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
    deviationLines: list[DeviationLine] = Field(default_factory=list)
    performanceLines: list[PerformanceLine] = Field(default_factory=list)

    constructionPlan: str = Field(default="")
    layoutPlan: str = Field(default="")
    powerPlan: str = Field(default="")
    omPlan: str = Field(default="")
    schedulePlan: str = Field(default="")
    techPlanNote: str = Field(default="")


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
