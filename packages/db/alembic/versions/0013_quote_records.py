"""AI 报价生成记录。

Revision ID: 0013_quote_records
Revises: 0012_role_menus
Create Date: 2026-09-17
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0013_quote_records"
down_revision: Union[str, None] = "0012_role_menus"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "quote_records" in set(inspector.get_table_names()):
        return
    op.create_table(
        "quote_records",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="主键"),
        sa.Column("public_id", sa.String(length=32), nullable=False, comment="对外ID"),
        sa.Column("user_id", sa.BigInteger(), nullable=False, comment="创建人用户ID"),
        sa.Column("username", sa.String(length=64), nullable=False, comment="创建人用户名"),
        sa.Column("project_name", sa.String(length=256), nullable=False, comment="项目名称"),
        sa.Column("note", sa.String(length=2000), nullable=False, comment="补充说明"),
        sa.Column("base_id", sa.String(length=32), nullable=False, comment="价目知识库 ID"),
        sa.Column("base_name", sa.String(length=128), nullable=False, comment="价目知识库名称"),
        sa.Column("tax_rate", sa.Float(), nullable=False, comment="税率"),
        sa.Column("total_ex_tax", sa.Float(), nullable=False, comment="不含税合计"),
        sa.Column("total_inc_tax", sa.Float(), nullable=False, comment="含税合计"),
        sa.Column("unmatched", sa.BigInteger(), nullable=False, comment="未匹配价目行数"),
        sa.Column("line_count", sa.BigInteger(), nullable=False, comment="报价行数"),
        sa.Column(
            "xlsx_file",
            sa.String(length=64),
            nullable=False,
            comment="storage/quotes 下 xlsx 文件名",
        ),
        sa.Column("download_name", sa.String(length=256), nullable=False, comment="下载显示名"),
        sa.Column("lines_json", mysql.JSON(), nullable=True, comment="报价明细快照"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
            comment="创建时间",
        ),
        sa.PrimaryKeyConstraint("id"),
        comment="AI 报价生成记录",
    )
    op.create_index("ix_quote_records_public_id", "quote_records", ["public_id"], unique=True)
    op.create_index("ix_quote_records_user_id", "quote_records", ["user_id"], unique=False)
    op.create_index("ix_quote_records_project_name", "quote_records", ["project_name"], unique=False)
    op.create_index("ix_quote_records_created_at", "quote_records", ["created_at"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "quote_records" not in set(inspector.get_table_names()):
        return
    op.drop_index("ix_quote_records_created_at", table_name="quote_records")
    op.drop_index("ix_quote_records_project_name", table_name="quote_records")
    op.drop_index("ix_quote_records_user_id", table_name="quote_records")
    op.drop_index("ix_quote_records_public_id", table_name="quote_records")
    op.drop_table("quote_records")
