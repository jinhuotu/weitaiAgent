from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field


class QuoteLineIn(BaseModel):
    seq: str = ""
    code: str = ""
    name: str = ""
    spec: str = ""
    unit: str = "项"
    qty: Decimal = Decimal("0")
    unitPrice: Decimal = Decimal("0")
    amount: Decimal = Decimal("0")
    source: str = "manual"
    matchName: str = ""
    note: str = ""


class GenerateQuoteIn(BaseModel):
    projectName: str = Field("", max_length=120)
    note: str = Field("", max_length=2000)
    taxRate: Decimal = Field(Decimal("0.13"), ge=0, le=1)
    baseId: str = Field("", max_length=32)
    baseName: str = Field("", max_length=128)
    lines: list[QuoteLineIn] = Field(default_factory=list)
