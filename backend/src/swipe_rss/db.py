"""SQLite engine with the settings from PROJECT_PLAN.md §3 Database.

Every transaction starts with BEGIN IMMEDIATE: a deferred transaction that
upgrades from read to write fails with SQLITE_BUSY_SNAPSHOT in WAL mode, and
busy_timeout cannot help. IMMEDIATE takes the write lock up front, so
busy_timeout applies.
"""

from pathlib import Path

from sqlalchemy import Engine, create_engine, event

BUSY_TIMEOUT_MS = 5000


def make_engine(db_path: Path | str) -> Engine:
    engine = create_engine(f"sqlite:///{db_path}")

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_connection, _connection_record):
        # Stop the sqlite3 driver from emitting its own (deferred) BEGIN.
        dbapi_connection.isolation_level = None
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()

    @event.listens_for(engine, "begin")
    def _on_begin(conn):
        conn.exec_driver_sql("BEGIN IMMEDIATE")

    return engine
