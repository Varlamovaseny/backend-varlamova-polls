import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

SCHEMA_FILE = Path(__file__).with_name("schema.sql")
PREFIX = "sqlite:///"


def db_path():
    url = os.environ.get("DATABASE_URL", PREFIX + "polls.db")
    if not url.startswith(PREFIX):
        raise RuntimeError(f"DATABASE_URL должен начинаться с {PREFIX}")
    return url[len(PREFIX):]


def connect():
    conn = sqlite3.connect(db_path(), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = connect()
    conn.executescript(SCHEMA_FILE.read_text(encoding="utf-8"))
    conn.close()


@contextmanager
def transaction(conn):
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise