from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():

    op.execute("ALTER TABLE polls ADD COLUMN owner_id INTEGER REFERENCES users (id) ON DELETE SET NULL")


def downgrade():

    op.execute(
        """
        CREATE TABLE polls_old (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            title       TEXT    NOT NULL,
            description TEXT    NOT NULL DEFAULT '',
            options     TEXT    NOT NULL,
            status      TEXT    NOT NULL DEFAULT 'open'
                        CHECK (status IN ('open', 'closed'))
        )
        """
    )
    op.execute("INSERT INTO polls_old (id, title, description, options, status) "
               "SELECT id, title, description, options, status FROM polls")
    op.execute("DROP TABLE polls")
    op.execute("ALTER TABLE polls_old RENAME TO polls")
