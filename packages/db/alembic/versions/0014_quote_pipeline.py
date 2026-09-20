"""报价流水线：purpose / 阶段 / 费率 / 核算与方案。

Revision ID: 0014_quote_pipeline
Revises: 0013_quote_records
Create Date: 2026-09-18
"""

from __future__ import annotations

import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0014_quote_pipeline"
down_revision: Union[str, None] = "0013_quote_records"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COLS = (
    ("purpose", sa.Column("purpose", sa.String(16), nullable=False, server_default="quote", comment="cost/quote/budget")),
    ("stage", sa.Column("stage", sa.String(32), nullable=False, server_default="quoted", comment="parsed/verified/costed/quoted")),
    ("location", sa.Column("location", sa.String(128), nullable=False, server_default="", comment="项目地点")),
    ("duration_days", sa.Column("duration_days", sa.Float(), nullable=True, comment="工期（天）")),
    ("bid_ceiling", sa.Column("bid_ceiling", sa.Float(), nullable=True, comment="招标限价（不含税）")),
    ("competition", sa.Column("competition", sa.String(32), nullable=False, server_default="balanced", comment="竞争档位")),
    ("target_margin", sa.Column("target_margin", sa.Float(), nullable=True, comment="目标毛利率")),
    ("rates_json", sa.Column("rates_json", mysql.JSON(), nullable=True, comment="费率快照")),
    ("verify_json", sa.Column("verify_json", mysql.JSON(), nullable=True, comment="核算报告")),
    ("schemes_json", sa.Column("schemes_json", mysql.JSON(), nullable=True, comment="报价方案")),
    ("instruction_text", sa.Column("instruction_text", sa.Text(), nullable=True, comment="报价编制说明")),
)

_EXTRA = ("/quotes-cost", "/quotes-budget")


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
    if "ix_quote_records_purpose" not in indexes:
        op.create_index("ix_quote_records_purpose", "quote_records", ["purpose"], unique=False)

    if "roles" in set(inspector.get_table_names()) and "menus" in {
        c["name"] for c in inspector.get_columns("roles")
    }:
        rows = bind.execute(sa.text("SELECT id, menus FROM roles")).fetchall()
        for rid, menus in rows:
            if not isinstance(menus, list):
                try:
                    menus = json.loads(menus) if menus else []
                except Exception:
                    menus = []
            if "/quotes" not in menus:
                continue
            changed = False
            for href in _EXTRA:
                if href not in menus:
                    menus.append(href)
                    changed = True
            if changed:
                bind.execute(
                    sa.text("UPDATE roles SET menus = CAST(:menus AS JSON) WHERE id = :id"),
                    {"menus": json.dumps(menus, ensure_ascii=False), "id": rid},
                )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "quote_records" not in set(inspector.get_table_names()):
        return
    indexes = {ix["name"] for ix in inspector.get_indexes("quote_records")}
    if "ix_quote_records_purpose" in indexes:
        op.drop_index("ix_quote_records_purpose", table_name="quote_records")
    existing = {c["name"] for c in inspector.get_columns("quote_records")}
    for name, _ in reversed(_COLS):
        if name in existing:
            op.drop_column("quote_records", name)
