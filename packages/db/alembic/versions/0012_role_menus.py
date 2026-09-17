"""角色表增加菜单白名单。

Revision ID: 0012_role_menus
Revises: 0011_tender_tech_docx
Create Date: 2026-09-17
"""

from __future__ import annotations

import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0012_role_menus"
down_revision: Union[str, None] = "0011_tender_tech_docx"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OPERATOR = ["/", "/ai-chat", "/work-tasks", "/tenders", "/tender-qa", "/quotes"]
_AUDITOR = ["/", "/ai-chat", "/tender-tasks", "/tender-qa", "/approval"]
_BUSINESS = [
    "/",
    "/ai-chat",
    "/work-tasks",
    "/tender-tasks",
    "/approval",
    "/knowledge",
    "/tenders",
    "/tender-qa",
    "/tender-library",
    "/quotes",
    "/settings",
]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "roles" not in tables:
        return
    cols = {c["name"] for c in inspector.get_columns("roles")}
    if "menus" not in cols:
        op.add_column(
            "roles",
            sa.Column("menus", mysql.JSON(), nullable=True, comment="可访问菜单 href 列表"),
        )

    rows = bind.execute(sa.text("SELECT id, code FROM roles")).fetchall()
    seed = {"operator": _OPERATOR, "auditor": _AUDITOR}
    for rid, code in rows:
        if code == "admin":
            continue
        payload = seed.get(code, _BUSINESS)
        bind.execute(
            sa.text("UPDATE roles SET menus = CAST(:menus AS JSON) WHERE id = :id"),
            {"menus": json.dumps(payload, ensure_ascii=False), "id": rid},
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "roles" not in tables:
        return
    cols = {c["name"] for c in inspector.get_columns("roles")}
    if "menus" in cols:
        op.drop_column("roles", "menus")
