"""knowledge_documents: content hash + OCR/page stats

Revision ID: 0003_kb_doc_hash_ocr
Revises: 0002_kb_prompts_mcp_agents
Create Date: 2026-08-18
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_kb_doc_hash_ocr"
down_revision: Union[str, None] = "0002_kb_prompts_mcp_agents"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "knowledge_documents",
        sa.Column("content_hash", sa.String(length=64), nullable=True, comment="原文件SHA-256"),
    )
    op.add_column(
        "knowledge_documents",
        sa.Column(
            "page_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="页/表数量",
        ),
    )
    op.add_column(
        "knowledge_documents",
        sa.Column(
            "ocr_pages",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="OCR调用次数",
        ),
    )
    op.add_column(
        "knowledge_documents",
        sa.Column(
            "ocr_capped",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("0"),
            comment="是否触达OCR页数上限",
        ),
    )
    op.add_column(
        "knowledge_documents",
        sa.Column(
            "formula_fallback",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("0"),
            comment="Excel是否回退公式原文",
        ),
    )
    op.create_index(
        "ix_knowledge_documents_base_hash",
        "knowledge_documents",
        ["base_id", "content_hash"],
    )


def downgrade() -> None:
    op.drop_index("ix_knowledge_documents_base_hash", table_name="knowledge_documents")
    op.drop_column("knowledge_documents", "formula_fallback")
    op.drop_column("knowledge_documents", "ocr_capped")
    op.drop_column("knowledge_documents", "ocr_pages")
    op.drop_column("knowledge_documents", "page_count")
    op.drop_column("knowledge_documents", "content_hash")
