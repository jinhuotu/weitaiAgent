"""投标文件生成记录与审批日志。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, String, Text, func
from sqlalchemy.dialects.mysql import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base


class TenderRecord(Base):
    """一次投标 Word 生成的元数据；正文文件在 storage/tenders。"""

    __tablename__ = "tender_records"
    __table_args__ = {"comment": "投标文件生成记录"}

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
    project_name: Mapped[str] = mapped_column(
        String(256), nullable=False, default="", index=True, comment="项目名称"
    )
    tenderer: Mapped[str] = mapped_column(
        String(256), nullable=False, default="", comment="招标人"
    )
    bid_price_yuan: Mapped[float] = mapped_column(
        Float, nullable=False, default=0, comment="投标总价（元）"
    )
    legal_person_name: Mapped[str] = mapped_column(
        String(64), nullable=False, default="", comment="法定代表人"
    )
    docx_file: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="storage/tenders 下 docx 文件名"
    )
    pdf_file: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="资质 PDF 副本文件名"
    )
    download_name: Mapped[str] = mapped_column(
        String(256), nullable=False, default="", comment="下载显示名"
    )
    pdf_download_name: Mapped[str | None] = mapped_column(
        String(256), nullable=True, comment="资质 PDF 下载名"
    )
    warnings: Mapped[list | None] = mapped_column(JSON, nullable=True, comment="生成警告")
    brief_json: Mapped[dict | None] = mapped_column(JSON, nullable=True, comment="生成时表单快照")
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="processing",
        server_default="processing",
        index=True,
        comment="processing/pending/approved/submitted/won/lost",
    )
    deadline: Mapped[str] = mapped_column(
        String(16), nullable=False, default="", comment="投标截止日期 YYYY-MM-DD"
    )
    project_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="", comment="项目类型"
    )
    current_step: Mapped[str] = mapped_column(
        String(32), nullable=False, default="", comment="当前审批环节"
    )
    submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="提交审批时间"
    )
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="最近审批决定时间"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
        comment="创建时间",
    )

    approval_logs: Mapped[list[TenderApprovalLog]] = relationship(
        "TenderApprovalLog",
        back_populates="record",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class TenderApprovalLog(Base):
    """投标任务审批流水。"""

    __tablename__ = "tender_approval_logs"
    __table_args__ = {"comment": "投标任务审批日志"}

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, comment="主键"
    )
    record_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("tender_records.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="tender_records.id",
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, index=True, comment="操作人用户ID"
    )
    username: Mapped[str] = mapped_column(
        String(64), nullable=False, default="", comment="操作人用户名"
    )
    action: Mapped[str] = mapped_column(
        String(16), nullable=False, comment="submit/pass/reject/mark"
    )
    step: Mapped[str] = mapped_column(
        String(32), nullable=False, default="", comment="当时环节"
    )
    comment: Mapped[str] = mapped_column(Text, nullable=False, default="", comment="意见")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
        comment="操作时间",
    )

    record: Mapped[TenderRecord] = relationship("TenderRecord", back_populates="approval_logs")
