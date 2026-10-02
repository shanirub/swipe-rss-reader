import sqlite3

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from swipe_rss.db import BUSY_TIMEOUT_MS


def test_pragmas(engine):
    with engine.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
        assert conn.exec_driver_sql("PRAGMA busy_timeout").scalar() == BUSY_TIMEOUT_MS
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1


def test_tables_are_strict(engine):
    with engine.begin() as conn, pytest.raises(IntegrityError):  # STRICT rejects text in an INTEGER column
        conn.execute(text("INSERT INTO feed_status (feed_id, consecutive_failures) VALUES ('a', 'not-a-number')"))


def test_transactions_take_write_lock_immediately(engine, db_path):
    """A transaction that only reads must still hold the write lock (BEGIN IMMEDIATE)."""
    with engine.begin() as conn:
        conn.execute(text("SELECT 1 FROM feed_status")).all()
        other = sqlite3.connect(db_path, timeout=0.1, isolation_level=None)
        try:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                other.execute("BEGIN IMMEDIATE")
        finally:
            other.close()
