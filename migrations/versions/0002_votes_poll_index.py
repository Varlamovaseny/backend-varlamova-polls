"""индекс по votes.poll_id для быстрого списка голосов опроса

Revision ID: 0002
Revises: 0001
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE INDEX IF NOT EXISTS idx_votes_poll_id ON votes (poll_id)")


def downgrade():
    op.execute("DROP INDEX IF EXISTS idx_votes_poll_id")