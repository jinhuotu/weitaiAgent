"""tender_records：投标文件生成历史。

Revision ID: 0008_tender_records
Revises: 0007_kb_review_ingest
Create Date: 2026-08-31
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0008_tender_records"
down_revision: Union[str, None] = "0007_kb_review_ingest"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tender_records",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="主键"),
        sa.Column("public_id", sa.String(length=32), nullable=False, comment="对外ID"),
        sa.Column("user_id", sa.BigInteger(), nullable=False, comment="创建人用户ID"),
        sa.Column("username", sa.String(length=64), nullable=False, comment="创建人用户名"),
        sa.Column("project_name", sa.String(length=256), nullable=False, comment="项目名称"),
        sa.Column("tenderer", sa.String(length=256), nullable=False, comment="招标人"),
        sa.Column("bid_price_yuan", sa.Float(), nullable=False, comment="投标总价（元）"),
        sa.Column("legal_person_name", sa.String(length=64), nullable=False, comment="法定代表人"),
        sa.Column(
            "docx_file",
            sa.String(length=64),
            nullable=False,
            comment="storage/tenders 下 docx 文件名",
        ),
        sa.Column("pdf_file", sa.String(length=64), nullable=True, comment="资质 PDF 副本文件名"),
        sa.Column("download_name", sa.String(length=256), nullable=False, comment="下载显示名"),
        sa.Column("pdf_download_name", sa.String(length=256), nullable=True, comment="资质 PDF 下载名"),
        sa.Column("warnings", mysql.JSON(), nullable=True, comment="生成警告"),
        sa.Column("brief_json", mysql.JSON(), nullable=True, comment="生成时表单快照"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
            comment="创建时间",
        ),
        sa.PrimaryKeyConstraint("id"),
        comment="投标文件生成记录",
    )
    op.create_index("ix_tender_records_public_id", "tender_records", ["public_id"], unique=True)
    op.create_index("ix_tender_records_user_id", "tender_records", ["user_id"], unique=False)
    op.create_index("ix_tender_records_project_name", "tender_records", ["project_name"], unique=False)
    op.create_index("ix_tender_records_created_at", "tender_records", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_tender_records_created_at", table_name="tender_records")
    op.drop_index("ix_tender_records_project_name", table_name="tender_records")
    op.drop_index("ix_tender_records_user_id", table_name="tender_records")
    op.drop_index("ix_tender_records_public_id", table_name="tender_records")
    op.drop_table("tender_records")
