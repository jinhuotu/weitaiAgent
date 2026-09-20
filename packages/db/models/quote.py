"""AI 造价 / 报价 / 预算生成记录。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Float, Integer, String, Text, func
from sqlalchemy.dialects.mysql import JSON
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


class QuoteRecord(Base):
    __tablename__ = "quote_records"
    __table_args__ = {"comment": "AI 造价/报价/预算生成记录"}

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, comment="主键"
    )
    public_id: Mapped[str] = mapped_column(
        String(32), unique=True, index=True, nullable=False, comment="对外ID"
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, index=True, comment="创建人用户ID"
    )
    username: Mapped[str] = mapped_column(
        String(64), nullable=False, default="", comment="创建人用户名"
    )
    purpose: Mapped[str] = mapped_column(
        String(16), nullable=False, default="quote", index=True, comment="cost/quote/budget"
    )
    stage: Mapped[str] = mapped_column(
        String(16), nullable=False, default="quoted", comment="parsed/verified/costed/quoted"
    )
    project_name: Mapped[str] = mapped_column(
        String(256), nullable=False, default="", index=True, comment="项目名称"
    )
    note: Mapped[str] = mapped_column(String(2000), nullable=False, default="", comment="补充说明")
    location: Mapped[str] = mapped_column(
        String(128), nullable=False, default="", comment="项目地点"
    )
    duration_days: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="工期天数"
    )
    bid_ceiling: Mapped[float | None] = mapped_column(
        Float, nullable=True, comment="招标限价(不含税)"
    )
    competition: Mapped[str] = mapped_column(
        String(32), nullable=False, default="balanced", comment="竞争档位"
    )
    target_margin: Mapped[float | None] = mapped_column(
        Float, nullable=True, comment="目标毛利率"
    )
    base_id: Mapped[str] = mapped_column(String(32), nullable=False, default="", comment="价目知识库 ID")
    base_name: Mapped[str] = mapped_column(
        String(128), nullable=False, default="", comment="价目知识库名称"
    )
    tax_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.13, comment="税率")
    total_ex_tax: Mapped[float] = mapped_column(Float, nullable=False, default=0, comment="不含税合计")
    total_inc_tax: Mapped[float] = mapped_column(Float, nullable=False, default=0, comment="含税合计")
    unmatched: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, comment="未匹配价目行数")
    line_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, comment="明细行数")
    xlsx_file: Mapped[str] = mapped_column(
        String(64), nullable=False, default="", comment="storage/quotes 下 xlsx 文件名"
    )
    download_name: Mapped[str] = mapped_column(
        String(256), nullable=False, default="", comment="下载显示名"
    )
    lines_json: Mapped[list | None] = mapped_column(JSON, nullable=True, comment="明细快照")
    rates_json: Mapped[dict | None] = mapped_column(JSON, nullable=True, comment="费率快照")
    verify_json: Mapped[dict | None] = mapped_column(JSON, nullable=True, comment="核算报告")
    schemes_json: Mapped[list | None] = mapped_column(JSON, nullable=True, comment="报价方案")
    instruction_text: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="编制说明"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        comment="创建时间",
    )
