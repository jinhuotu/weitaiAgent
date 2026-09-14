"""投标审批流程定义（超级管理员可配置）。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String, func
from sqlalchemy.dialects.mysql import JSON
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


class ApprovalFlow(Base):
    __tablename__ = "approval_flows"
    __table_args__ = {"comment": "审批流程定义"}

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, comment="主键"
    )
    code: Mapped[str] = mapped_column(
        String(32), unique=True, nullable=False, comment="流程编码，如 tender"
    )
    steps: Mapped[list | dict] = mapped_column(JSON, nullable=False, comment="步骤 JSON")
    updated_by: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, comment="最后修改人用户ID"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        comment="创建时间",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
        comment="更新时间",
    )
