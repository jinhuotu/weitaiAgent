"""Persist dismissal of virtual tender-library slots."""
import sqlalchemy as sa
from alembic import op

revision = '0017_library_dismissals'
down_revision = '0016_tender_bid_marks'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'tender_library_dismissals',
        sa.Column('base_public_id', sa.String(32), primary_key=True),
        sa.Column('key', sa.String(96), primary_key=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )


def downgrade():
    op.drop_table('tender_library_dismissals')
