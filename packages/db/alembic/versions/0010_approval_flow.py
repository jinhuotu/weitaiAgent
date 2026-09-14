"""审批流程定义表 + 投标记录流程快照。

Revision ID: 0010_approval_flow
Revises: 0009_tender_workflow
Create Date: 2026-09-14
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0010_approval_flow"
down_revision: Union[str, None] = "0009_tender_workflow"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "approval_flows" not in tables:
        op.create_table(
            "approval_flows",
            sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="主键"),
            sa.Column("code", sa.String(length=32), nullable=False, comment="流程编码，如 tender"),
            sa.Column("steps", mysql.JSON(), nullable=False, comment="步骤 JSON"),
            sa.Column("updated_by", sa.BigInteger(), nullable=True, comment="最后修改人用户ID"),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
                comment="创建时间",
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
                comment="更新时间",
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("code"),
            comment="审批流程定义",
        )

    if "tender_records" in tables:
        cols = {c["name"] for c in inspector.get_columns("tender_records")}
        if "approval_snapshot" not in cols:
            op.add_column(
                "tender_records",
                sa.Column(
                    "approval_snapshot",
                    mysql.JSON(),
                    nullable=True,
                    comment="提交时的审批流程快照",
                ),
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "tender_records" in tables:
        cols = {c["name"] for c in inspector.get_columns("tender_records")}
        if "approval_snapshot" in cols:
            op.drop_column("tender_records", "approval_snapshot")
    if "approval_flows" in tables:
        op.drop_table("approval_flows")
