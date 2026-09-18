"""知识库与文档元数据。向量正文存在 Qdrant，本表只存卡片信息。"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.mysql import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base


class KnowledgeBase(Base):
    """用户自定义知识库（卡片列表实体）。"""

    __tablename__ = "knowledge_bases"
    __table_args__ = {"comment": "知识库"}

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, comment="主键"
    )
    public_id: Mapped[str] = mapped_column(
        String(32), unique=True, index=True, nullable=False, comment="对外ID"
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False, comment="知识库名称")
    description: Mapped[str | None] = mapped_column(String(512), nullable=True, comment="描述")
    purpose: Mapped[str] = mapped_column(
        String(16), nullable=False, default="rag", index=True, comment="用途：rag=AI知识库"
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active", comment="状态")
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True, comment="创建人用户ID")
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

    documents: Mapped[list[KnowledgeDocument]] = relationship(
        "KnowledgeDocument",
        back_populates="base",
        lazy="selectin",
    )
    acl_entries: Mapped[list[KnowledgeBaseAcl]] = relationship(
        "KnowledgeBaseAcl",
        back_populates="base",
        lazy="selectin",
        cascade="all, delete-orphan",
    )
    ingest_tasks: Mapped[list["KnowledgeIngestTask"]] = relationship(
        "KnowledgeIngestTask",
        back_populates="base",
        lazy="selectin",
        cascade="all, delete-orphan",
    )


class KnowledgeBaseAcl(Base):
    """知识库访问授权（查看 / 使用 / 维护）。"""

    __tablename__ = "knowledge_base_acl"
    __table_args__ = (
        sa.UniqueConstraint("base_id", "subject_type", "subject_id", name="uq_kb_acl_subject"),
        {"comment": "知识库访问授权"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, comment="主键"
    )
    base_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="知识库ID",
    )
    subject_type: Mapped[str] = mapped_column(
        String(16), nullable=False, comment="主体：user / role"
    )
    subject_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, comment="users.id 或 roles.id"
    )
    can_view: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, comment="列表与详情可见"
    )
    can_use: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, comment="对话检索 / 语义检索"
    )
    can_manage: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, comment="改库、导入删除资料、分配权限"
    )

    base: Mapped[KnowledgeBase] = relationship("KnowledgeBase", back_populates="acl_entries")


class KnowledgeDocument(Base):
    """知识库文档元数据（向量存 Qdrant）。"""

    __tablename__ = "knowledge_documents"
    __table_args__ = {"comment": "知识库文档元数据"}

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, comment="主键"
    )
    public_id: Mapped[str] = mapped_column(
        String(32), unique=True, index=True, nullable=False, comment="对外ID"
    )
    base_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所属知识库ID",
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, comment="资料名称")
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, default="text", comment="来源：file/text/url"
    )
    kind: Mapped[str] = mapped_column(
        String(16), nullable=False, default="doc", comment="类型：doc/drawing/3d/video"
    )
    parent_id: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
        index=True,
        comment="父文档对外ID（案例卡挂图纸附件）",
    )
    file_type: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="文件扩展名")
    size: Mapped[int | None] = mapped_column(BigInteger, nullable=True, comment="文件大小(字节)")
    url: Mapped[str | None] = mapped_column(String(1024), nullable=True, comment="来源URL")
    storage_path: Mapped[str | None] = mapped_column(String(512), nullable=True, comment="存储相对路径")
    file_key: Mapped[str | None] = mapped_column(String(512), nullable=True, comment="文件键")
    preview_url: Mapped[str | None] = mapped_column(String(2048), nullable=True, comment="预览地址")
    summary: Mapped[str | None] = mapped_column(Text, nullable=True, comment="摘要")
    char_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="字符数")
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="向量切块数")
    content_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True, comment="原文件SHA-256"
    )
    page_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="页/表数量")
    ocr_pages: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="OCR调用次数")
    ocr_capped: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, comment="是否触达OCR页数上限"
    )
    formula_fallback: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, comment="Excel是否回退公式原文"
    )
    tags: Mapped[list | None] = mapped_column(JSON, nullable=True, comment="标签JSON")
    uploader: Mapped[str | None] = mapped_column(String(128), nullable=True, comment="上传人")
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="parsing", comment="状态：parsing/ready/failed"
    )
    review_status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="pending",
        index=True,
        comment="审核：pending/approved/rejected",
    )
    review_comment: Mapped[str | None] = mapped_column(Text, nullable=True, comment="最近一次审核意见")
    reviewed_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True, comment="审核人用户ID")
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="审核时间"
    )
    error_msg: Mapped[str | None] = mapped_column(Text, nullable=True, comment="失败原因")
    created_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True, comment="创建人用户ID")
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

    base: Mapped[KnowledgeBase] = relationship("KnowledgeBase", back_populates="documents")
    reviews: Mapped[list["KnowledgeDocumentReview"]] = relationship(
        "KnowledgeDocumentReview",
        back_populates="document",
        lazy="selectin",
        cascade="all, delete-orphan",
    )
    ingest_tasks: Mapped[list["KnowledgeIngestTask"]] = relationship(
        "KnowledgeIngestTask",
        back_populates="document",
        lazy="selectin",
        cascade="all, delete-orphan",
    )


class KnowledgeDocumentReview(Base):
    """资料审核流水。"""

    __tablename__ = "knowledge_document_reviews"
    __table_args__ = {"comment": "知识库资料审核流水"}

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, comment="主键"
    )
    document_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("knowledge_documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="文档ID",
    )
    action: Mapped[str] = mapped_column(
        String(16), nullable=False, comment="动作：approve / reject"
    )
    comment: Mapped[str | None] = mapped_column(Text, nullable=True, comment="意见")
    actor_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, comment="操作人用户ID")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        comment="创建时间",
    )

    document: Mapped[KnowledgeDocument] = relationship(
        "KnowledgeDocument", back_populates="reviews"
    )


class KnowledgeIngestTask(Base):
    """文件入库后台任务（进程内串行 worker）。"""

    __tablename__ = "knowledge_ingest_tasks"
    __table_args__ = {"comment": "知识库入库任务"}

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True, comment="主键"
    )
    public_id: Mapped[str] = mapped_column(
        String(32), unique=True, index=True, nullable=False, comment="对外ID"
    )
    base_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="知识库ID",
    )
    document_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("knowledge_documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="文档ID",
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="queued",
        index=True,
        comment="queued/running/succeeded/failed/cancelled",
    )
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="进度 0-100")
    error_msg: Mapped[str | None] = mapped_column(Text, nullable=True, comment="失败原因")
    force_reextract: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, comment="是否强制重抽正文"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        comment="创建时间",
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="开始时间"
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="结束时间"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
        comment="更新时间",
    )

    base: Mapped[KnowledgeBase] = relationship("KnowledgeBase", back_populates="ingest_tasks")
    document: Mapped[KnowledgeDocument] = relationship(
        "KnowledgeDocument", back_populates="ingest_tasks"
    )
