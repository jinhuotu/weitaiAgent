"""knowledge_documents review + ingest tasks + review audit.

Revision ID: 0007_kb_review_ingest
Revises: 0006_kb_acl
Create Date: 2026-08-29
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007_kb_review_ingest"
down_revision: Union[str, None] = "0006_kb_acl"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    cols = {c["name"] for c in inspector.get_columns("knowledge_documents")}
    if "review_status" not in cols:
        op.add_column(
            "knowledge_documents",
            sa.Column(
                "review_status",
                sa.String(length=16),
                nullable=False,
                server_default="pending",
                comment="审核：pending/approved/rejected",
            ),
        )
        op.create_index(
            "ix_knowledge_documents_review_status",
            "knowledge_documents",
            ["review_status"],
        )
    if "review_comment" not in cols:
        op.add_column(
            "knowledge_documents",
            sa.Column("review_comment", sa.Text(), nullable=True, comment="最近一次审核意见"),
        )
    if "reviewed_by" not in cols:
        op.add_column(
            "knowledge_documents",
            sa.Column("reviewed_by", sa.BigInteger(), nullable=True, comment="审核人用户ID"),
        )
    if "reviewed_at" not in cols:
        op.add_column(
            "knowledge_documents",
            sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True, comment="审核时间"),
        )
    op.execute(
        sa.text(
            "UPDATE knowledge_documents SET review_status = 'approved' "
            "WHERE status = 'ready' OR kind = 'drawing'"
        )
    )

    if not inspector.has_table("knowledge_document_reviews"):
        op.create_table(
            "knowledge_document_reviews",
            sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="主键"),
            sa.Column("document_id", sa.BigInteger(), nullable=False, comment="文档ID"),
            sa.Column(
                "action",
                sa.String(length=16),
                nullable=False,
                comment="动作：approve / reject",
            ),
            sa.Column("comment", sa.Text(), nullable=True, comment="意见"),
            sa.Column("actor_id", sa.BigInteger(), nullable=True, comment="操作人用户ID"),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
                comment="创建时间",
            ),
            sa.ForeignKeyConstraint(
                ["document_id"], ["knowledge_documents.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("id"),
            comment="知识库资料审核流水",
        )
        op.create_index(
            "ix_knowledge_document_reviews_document_id",
            "knowledge_document_reviews",
            ["document_id"],
        )

    if not inspector.has_table("knowledge_ingest_tasks"):
        op.create_table(
            "knowledge_ingest_tasks",
            sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="主键"),
            sa.Column(
                "public_id",
                sa.String(length=32),
                nullable=False,
                comment="对外ID",
            ),
            sa.Column("base_id", sa.BigInteger(), nullable=False, comment="知识库ID"),
            sa.Column("document_id", sa.BigInteger(), nullable=False, comment="文档ID"),
            sa.Column(
                "status",
                sa.String(length=16),
                nullable=False,
                server_default="queued",
                comment="queued/running/succeeded/failed/cancelled",
            ),
            sa.Column(
                "progress",
                sa.Integer(),
                nullable=False,
                server_default="0",
                comment="进度 0-100",
            ),
            sa.Column("error_msg", sa.Text(), nullable=True, comment="失败原因"),
            sa.Column(
                "force_reextract",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("0"),
                comment="是否强制重抽正文",
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
                comment="创建时间",
            ),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True, comment="开始时间"),
            sa.Column(
                "finished_at", sa.DateTime(timezone=True), nullable=True, comment="结束时间"
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
                comment="更新时间",
            ),
            sa.ForeignKeyConstraint(["base_id"], ["knowledge_bases.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(
                ["document_id"], ["knowledge_documents.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("public_id"),
            comment="知识库入库任务",
        )
        op.create_index(
            "ix_knowledge_ingest_tasks_base_id", "knowledge_ingest_tasks", ["base_id"]
        )
        op.create_index(
            "ix_knowledge_ingest_tasks_document_id",
            "knowledge_ingest_tasks",
            ["document_id"],
        )
        op.create_index(
            "ix_knowledge_ingest_tasks_status", "knowledge_ingest_tasks", ["status"]
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("knowledge_ingest_tasks"):
        op.drop_index("ix_knowledge_ingest_tasks_status", table_name="knowledge_ingest_tasks")
        op.drop_index(
            "ix_knowledge_ingest_tasks_document_id", table_name="knowledge_ingest_tasks"
        )
        op.drop_index("ix_knowledge_ingest_tasks_base_id", table_name="knowledge_ingest_tasks")
        op.drop_table("knowledge_ingest_tasks")
    if inspector.has_table("knowledge_document_reviews"):
        op.drop_index(
            "ix_knowledge_document_reviews_document_id",
            table_name="knowledge_document_reviews",
        )
        op.drop_table("knowledge_document_reviews")
    cols = {c["name"] for c in inspector.get_columns("knowledge_documents")}
    if "review_status" in cols:
        op.drop_index("ix_knowledge_documents_review_status", table_name="knowledge_documents")
        op.drop_column("knowledge_documents", "review_status")
    if "review_comment" in cols:
        op.drop_column("knowledge_documents", "review_comment")
    if "reviewed_by" in cols:
        op.drop_column("knowledge_documents", "reviewed_by")
    if "reviewed_at" in cols:
        op.drop_column("knowledge_documents", "reviewed_at")
