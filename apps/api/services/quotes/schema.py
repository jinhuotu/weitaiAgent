from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field


class QuoteLineIn(BaseModel):
    seq: str = ""
    code: str = ""
    name: str = ""
    spec: str = ""
    unit: str = "项"
    qty: Decimal = Decimal("0")
    unitPrice: Decimal = Decimal("0")
    costPrice: Decimal = Decimal("0")
    sellPrice: Decimal = Decimal("0")
    amount: Decimal = Decimal("0")
    source: str = "manual"
    matchName: str = ""
    note: str = ""


class QuoteRatesIn(BaseModel):
    laborCoef: Decimal = Field(Decimal("0"), ge=0)
    measureRate: Decimal = Field(Decimal("0.03"), ge=0, le=1)
    manageRate: Decimal = Field(Decimal("0.05"), ge=0, le=1)
    profitRate: Decimal = Field(Decimal("0.08"), ge=0, le=1)
    taxRate: Decimal = Field(Decimal("0.13"), ge=0, le=1)
    budgetCoef: Decimal = Field(Decimal("1.05"), ge=0)
    contingencyRate: Decimal = Field(Decimal("0.03"), ge=0, le=1)


class GenerateQuoteIn(BaseModel):
    purpose: Literal["cost", "quote", "budget"] = "quote"
    projectName: str = Field("", max_length=120)
    note: str = Field("", max_length=2000)
    location: str = Field("", max_length=128)
    durationDays: float | None = None
    bidCeiling: Decimal | None = Field(None, ge=0)
    competition: Literal["conservative", "balanced", "aggressive"] = "balanced"
    targetMargin: Decimal | None = Field(None, ge=0, le=1)
    taxRate: Decimal = Field(Decimal("0.13"), ge=0, le=1)
    baseId: str = Field("", max_length=32)
    baseName: str = Field("", max_length=128)
    rates: QuoteRatesIn | None = None
    applyVerifyFixes: bool = False
    lines: list[QuoteLineIn] = Field(default_factory=list)


class VerifyQuoteIn(BaseModel):
    lines: list[QuoteLineIn] = Field(default_factory=list)
    applyFixes: bool = False


class CostQuoteIn(BaseModel):
    lines: list[QuoteLineIn] = Field(default_factory=list)
    rates: QuoteRatesIn | None = None
