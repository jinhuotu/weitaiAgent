"""Add optional document validity metadata."""
import sqlalchemy as sa
from alembic import op
revision = '0018_document_expiry'
down_revision = '0017_library_dismissals'
branch_labels = None
depends_on = None

def upgrade():
    op.add_column('knowledge_documents', sa.Column('expire_at', sa.Date(), nullable=True))
    op.add_column('knowledge_documents', sa.Column('is_perpetual', sa.Boolean(), nullable=True, server_default=sa.false()))

def downgrade():
    op.drop_column('knowledge_documents', 'is_perpetual')
    op.drop_column('knowledge_documents', 'expire_at')
