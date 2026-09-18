"""SQLite connection helpers: WAL mode, busy_timeout, per-worker connections."""
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

BUSY_TIMEOUT_MS = 5000


def resolve_db_path(path: str | None = None) -> Path:
    from app.config import settings
    raw = path or settings.database_path
    p = Path(raw)
    if not p.is_absolute():
        p = Path.cwd() / p
    return p


def connect(path: str | None = None) -> sqlite3.Connection:
    db_path = resolve_db_path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30.0, isolation_level=None,
                           check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


@contextmanager
def get_conn(path: str | None = None):
    conn = connect(path)
    try:
        yield conn
    finally:
        conn.close()


def transaction(conn: sqlite3.Connection):
    return conn  # autocommit off via BEGIN
