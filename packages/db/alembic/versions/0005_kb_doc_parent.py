"""knowledge_documents.parent_id：案例卡挂图纸附件

Revision ID: 0005_kb_doc_parent
Revises: 0004_chat_message_images
Create Date: 2026-08-19
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005_kb_doc_parent"
down_revision: Union[str, None] = "0004_chat_message_images"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "knowledge_documents",
        sa.Column(
            "parent_id",
            sa.String(length=32),
            nullable=True,
            comment="父文档对外ID（案例卡挂图纸）",
        ),
    )
    op.create_index("ix_knowledge_documents_parent_id", "knowledge_documents", ["parent_id"])


def downgrade() -> None:
    op.drop_index("ix_knowledge_documents_parent_id", table_name="knowledge_documents")
    op.drop_column("knowledge_documents", "parent_id")
