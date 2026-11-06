CREATE TABLE IF NOT EXISTS polls (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT    NOT NULL,
    description TEXT    NOT NULL DEFAULT '',
    options     TEXT    NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'open'
                CHECK (status IN ('open', 'closed'))
);

CREATE TABLE IF NOT EXISTS votes (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    poll_id INTEGER NOT NULL REFERENCES polls (id) ON DELETE CASCADE,
    option  TEXT    NOT NULL,
    email   TEXT    NOT NULL
);