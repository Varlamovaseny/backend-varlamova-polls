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


def ensure_schema():
    conn = connect()
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'polls'"
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise RuntimeError("В базе нет таблиц. Создай их командой: alembic upgrade head")


@contextmanager
def transaction(conn):
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise