"""历史标书章节好坏标记。

Revision ID: 0016_tender_bid_marks
Revises: 0015_quote_lock
Create Date: 2026-10-23
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0016_tender_bid_marks"
down_revision: Union[str, None] = "0015_quote_lock"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tender_bid_marks",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="主键"),
        sa.Column("public_id", sa.String(length=32), nullable=False, comment="对外ID"),
        sa.Column(
            "record_id",
            sa.BigInteger(),
            nullable=True,
            comment="tender_records.id，上传未关联记录时为空",
        ),
        sa.Column("doc_public_id", sa.String(length=32), nullable=True, comment="历史标书章节文档ID"),
        sa.Column("volume", sa.String(length=16), nullable=False, comment="business/technical"),
        sa.Column("chapter_title", sa.String(length=40), nullable=False, comment="脱敏后的章节标题"),
        sa.Column("verdict", sa.String(length=8), nullable=False, comment="good/bad/none"),
        sa.Column("problem_tags", mysql.JSON(), nullable=True, comment="问题标签，封闭集合"),
        sa.Column("note", sa.String(length=500), nullable=False, comment="补充说明，已脱敏"),
        sa.Column("source", sa.String(length=16), nullable=False, comment="auto/approval/manual"),
        sa.Column("created_by", sa.BigInteger(), nullable=True, comment="标记人用户ID"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
            comment="创建时间",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
            comment="更新时间",
        ),
        sa.ForeignKeyConstraint(["record_id"], ["tender_records.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("public_id"),
        sa.UniqueConstraint("doc_public_id"),
        sa.UniqueConstraint("record_id", "volume", "chapter_title", name="uq_bid_mark_chapter"),
        comment="历史标书章节好坏标记",
    )
    op.create_index("ix_tender_bid_marks_record_id", "tender_bid_marks", ["record_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_tender_bid_marks_record_id", table_name="tender_bid_marks")
    op.drop_table("tender_bid_marks")
