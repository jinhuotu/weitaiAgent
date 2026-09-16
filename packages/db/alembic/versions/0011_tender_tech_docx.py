"""投标记录增加技术标 Word 文件名。

Revision ID: 0011_tender_tech_docx
Revises: 0010_approval_flow
Create Date: 2026-09-16
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0011_tender_tech_docx"
down_revision: Union[str, None] = "0010_approval_flow"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "tender_records" not in tables:
        return
    cols = {c["name"] for c in inspector.get_columns("tender_records")}
    if "tech_docx_file" not in cols:
        op.add_column(
            "tender_records",
            sa.Column(
                "tech_docx_file",
                sa.String(length=64),
                nullable=True,
                comment="storage/tenders 下技术标 docx 文件名",
            ),
        )
    if "tech_download_name" not in cols:
        op.add_column(
            "tender_records",
            sa.Column(
                "tech_download_name",
                sa.String(length=256),
                nullable=True,
                comment="技术标下载显示名",
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "tender_records" not in tables:
        return
    cols = {c["name"] for c in inspector.get_columns("tender_records")}
    if "tech_download_name" in cols:
        op.drop_column("tender_records", "tech_download_name")
    if "tech_docx_file" in cols:
        op.drop_column("tender_records", "tech_docx_file")
