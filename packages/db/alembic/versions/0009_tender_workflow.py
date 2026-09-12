"""tender_records 工作流字段 + tender_approval_logs。

Revision ID: 0009_tender_workflow
Revises: 0008_tender_records
Create Date: 2026-09-09
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0009_tender_workflow"
down_revision: Union[str, None] = "0008_tender_records"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "tender_records" in tables:
        cols = {c["name"] for c in inspector.get_columns("tender_records")}
        if "status" not in cols:
            op.add_column(
                "tender_records",
                sa.Column(
                    "status",
                    sa.String(length=16),
                    nullable=False,
                    server_default="processing",
                    comment="processing/pending/approved/submitted/won/lost",
                ),
            )
            op.create_index("ix_tender_records_status", "tender_records", ["status"], unique=False)
        if "deadline" not in cols:
            op.add_column(
                "tender_records",
                sa.Column(
                    "deadline",
                    sa.String(length=16),
                    nullable=False,
                    server_default="",
                    comment="投标截止日期 YYYY-MM-DD",
                ),
            )
        if "project_type" not in cols:
            op.add_column(
                "tender_records",
                sa.Column(
                    "project_type",
                    sa.String(length=32),
                    nullable=False,
                    server_default="",
                    comment="项目类型",
                ),
            )
        if "current_step" not in cols:
            op.add_column(
                "tender_records",
                sa.Column(
                    "current_step",
                    sa.String(length=32),
                    nullable=False,
                    server_default="",
                    comment="当前审批环节",
                ),
            )
        if "submitted_at" not in cols:
            op.add_column(
                "tender_records",
                sa.Column(
                    "submitted_at",
                    sa.DateTime(timezone=True),
                    nullable=True,
                    comment="提交审批时间",
                ),
            )
        if "decided_at" not in cols:
            op.add_column(
                "tender_records",
                sa.Column(
                    "decided_at",
                    sa.DateTime(timezone=True),
                    nullable=True,
                    comment="最近审批决定时间",
                ),
            )

    if "tender_approval_logs" not in tables:
        op.create_table(
            "tender_approval_logs",
            sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="主键"),
            sa.Column(
                "record_id",
                sa.BigInteger(),
                nullable=False,
                comment="tender_records.id",
            ),
            sa.Column("user_id", sa.BigInteger(), nullable=False, comment="操作人用户ID"),
            sa.Column(
                "username",
                sa.String(length=64),
                nullable=False,
                comment="操作人用户名",
            ),
            sa.Column(
                "action",
                sa.String(length=16),
                nullable=False,
                comment="submit/pass/reject/mark",
            ),
            sa.Column(
                "step",
                sa.String(length=32),
                nullable=False,
                comment="当时环节",
            ),
            sa.Column("comment", mysql.TEXT(), nullable=False, comment="意见"),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("CURRENT_TIMESTAMP"),
                nullable=False,
                comment="操作时间",
            ),
            sa.ForeignKeyConstraint(["record_id"], ["tender_records.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            comment="投标任务审批日志",
        )
        op.create_index(
            "ix_tender_approval_logs_record_id",
            "tender_approval_logs",
            ["record_id"],
            unique=False,
        )
        op.create_index(
            "ix_tender_approval_logs_user_id",
            "tender_approval_logs",
            ["user_id"],
            unique=False,
        )
        op.create_index(
            "ix_tender_approval_logs_created_at",
            "tender_approval_logs",
            ["created_at"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "tender_approval_logs" in tables:
        op.drop_index("ix_tender_approval_logs_created_at", table_name="tender_approval_logs")
        op.drop_index("ix_tender_approval_logs_user_id", table_name="tender_approval_logs")
        op.drop_index("ix_tender_approval_logs_record_id", table_name="tender_approval_logs")
        op.drop_table("tender_approval_logs")
    if "tender_records" in tables:
        cols = {c["name"] for c in inspector.get_columns("tender_records")}
        if "status" in cols:
            op.drop_index("ix_tender_records_status", table_name="tender_records")
            op.drop_column("tender_records", "status")
        for name in ("decided_at", "submitted_at", "current_step", "project_type", "deadline"):
            if name in cols:
                op.drop_column("tender_records", name)
