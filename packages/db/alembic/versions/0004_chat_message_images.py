"""chat_messages.images JSON

Revision ID: 0004_chat_message_images
Revises: 0003_kb_doc_hash_ocr
Create Date: 2026-08-19
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0004_chat_message_images"
down_revision: Union[str, None] = "0003_kb_doc_hash_ocr"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "chat_messages",
        sa.Column("images", mysql.JSON(), nullable=True, comment="附图元数据JSON"),
    )


def downgrade() -> None:
    op.drop_column("chat_messages", "images")
