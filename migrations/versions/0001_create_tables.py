"""создаём таблицы polls и votes

Revision ID: 0001
Revises:
"""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS polls (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            title       TEXT    NOT NULL,
            description TEXT    NOT NULL DEFAULT '',
            options     TEXT    NOT NULL,
            status      TEXT    NOT NULL DEFAULT 'open'
                        CHECK (status IN ('open', 'closed'))
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS votes (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            poll_id INTEGER NOT NULL REFERENCES polls (id) ON DELETE CASCADE,
            option  TEXT    NOT NULL,
            email   TEXT    NOT NULL
        )
        """
    )


def downgrade():
    op.execute("DROP TABLE IF EXISTS votes")
    op.execute("DROP TABLE IF EXISTS polls")