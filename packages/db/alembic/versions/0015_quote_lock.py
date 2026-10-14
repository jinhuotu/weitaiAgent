"""报价签认锁定、采用方案、价目快照。

Revision ID: 0015_quote_lock
Revises: 0014_quote_pipeline
Create Date: 2026-10-09
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0015_quote_lock"
down_revision: Union[str, None] = "0014_quote_pipeline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLS = (
    (
        "status",
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default="draft",
            comment="draft/locked",
        ),
    ),
    (
        "scheme_name",
        sa.Column("scheme_name", sa.String(32), nullable=False, server_default="", comment="采用的报价方案"),
    ),
    (
        "locked_at",
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True, comment="签认锁定时间"),
    ),
    (
        "locked_by",
        sa.Column("locked_by", sa.String(64), nullable=False, server_default="", comment="签认人"),
    ),
    (
        "catalog_json",
        sa.Column("catalog_json", mysql.JSON(), nullable=True, comment="价目快照（文件名/条数）"),
    ),
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "quote_records" not in set(inspector.get_table_names()):
        return
    existing = {c["name"] for c in inspector.get_columns("quote_records")}
    for name, col in _COLS:
        if name not in existing:
            op.add_column("quote_records", col)
    indexes = {ix["name"] for ix in inspector.get_indexes("quote_records")}
    if "ix_quote_records_status" not in indexes:
        op.create_index("ix_quote_records_status", "quote_records", ["status"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "quote_records" not in set(inspector.get_table_names()):
        return
    indexes = {ix["name"] for ix in inspector.get_indexes("quote_records")}
    if "ix_quote_records_status" in indexes:
        op.drop_index("ix_quote_records_status", table_name="quote_records")
    existing = {c["name"] for c in inspector.get_columns("quote_records")}
    for name, _ in reversed(_COLS):
        if name in existing:
            op.drop_column("quote_records", name)
