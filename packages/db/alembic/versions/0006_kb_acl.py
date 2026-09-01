"""knowledge_base_acl：按用户/角色授权查看、使用、维护知识库。

Revision ID: 0006_kb_acl
Revises: 0005_kb_doc_parent
Create Date: 2026-08-26
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006_kb_acl"
down_revision: Union[str, None] = "0005_kb_doc_parent"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("knowledge_base_acl"):
        return
    op.create_table(
        "knowledge_base_acl",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="主键"),
        sa.Column("base_id", sa.BigInteger(), nullable=False, comment="知识库ID"),
        sa.Column("subject_type", sa.String(length=16), nullable=False, comment="主体：user / role"),
        sa.Column("subject_id", sa.BigInteger(), nullable=False, comment="users.id 或 roles.id"),
        sa.Column(
            "can_view",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("1"),
            comment="列表与详情可见",
        ),
        sa.Column(
            "can_use",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("0"),
            comment="对话检索 / 语义检索",
        ),
        sa.Column(
            "can_manage",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("0"),
            comment="改库、导入删除资料、分配权限",
        ),
        sa.ForeignKeyConstraint(["base_id"], ["knowledge_bases.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("base_id", "subject_type", "subject_id", name="uq_kb_acl_subject"),
        comment="知识库访问授权",
    )
    op.create_index("ix_knowledge_base_acl_base_id", "knowledge_base_acl", ["base_id"])


def downgrade() -> None:
    op.drop_index("ix_knowledge_base_acl_base_id", table_name="knowledge_base_acl")
    op.drop_table("knowledge_base_acl")
