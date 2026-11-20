import os
import sqlite3
from contextlib import contextmanager

from dotenv import load_dotenv

load_dotenv()

PREFIX = "sqlite:///"


def db_path():
    url = os.environ.get("DATABASE_URL", PREFIX + "polls.db")
    if not url.startswith(PREFIX):
        raise RuntimeError(f"DATABASE_URL должен начинаться с {PREFIX}")
    return url[len(PREFIX):]


def py_lower(value):
    return value.lower() if isinstance(value, str) else value


def connect():

    conn = sqlite3.connect(db_path(), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")


    conn.create_function("py_lower", 1, py_lower, deterministic=True)
    return conn


REQUIRED_COLUMNS = {
    "polls": {"id", "title", "description", "options", "status", "owner_id"},
    "votes": {"id", "poll_id", "option", "email"},
    "users": {"id", "username", "password_hash", "role"},
}


def ensure_schema():
    conn = connect()
    try:
        missing = False
        for table, columns in REQUIRED_COLUMNS.items():
            existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
            if not columns <= existing:
                missing = True
    finally:
        conn.close()
    if missing:
        raise RuntimeError("Схема базы устарела или не создана. Выполни: alembic upgrade head")


@contextmanager
def transaction(conn):
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise