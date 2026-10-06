"""On-server backups, layer 1 (PROJECT_PLAN.md §3 Backups).

Snapshots go into a host directory outside the repo and outside every Docker volume, so a
`docker compose down -v` or a volume prune can't take them along. A snapshot is made with
VACUUM INTO: a consistent copy of the live database including writes still in the WAL file
(a plain copy of swipe_rss.db would miss those).

A snapshot counts only after it has been opened as a separate, read-only database and queried
(integrity check, migration version, row count of every table). The production database is only
ever read, by VACUUM INTO.

Rotation keeps every snapshot of the last 48 hours plus the midnight snapshot of each day for
14 days, and only ever deletes files whose names it generated itself. Restoring is manual for now
(README); see PROJECT_PLAN.md §7 for later ideas.
"""

import re
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

KEEP_ALL = timedelta(hours=48)
KEEP_MIDNIGHT = timedelta(days=14)
_NAME = re.compile(r"swipe_rss-(\d{8}T\d{4})Z\.db")
_FORMAT = "%Y%m%dT%H%M"
TABLES = ("feed_status", "items", "tombstones", "swipes", "saved")


class BackupError(Exception):
    pass


def snapshot_name(now: datetime) -> str:
    return f"swipe_rss-{now.astimezone(UTC).strftime(_FORMAT)}Z.db"


def verify(path: Path) -> dict[str, str | int]:
    """Opens `path` as a separate, read-only database and queries it: integrity check, migration
    version, row count of every table. Returns the version and counts; raises BackupError if any
    of it fails."""
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
            result = conn.execute("PRAGMA quick_check").fetchone()[0]
            if result != "ok":
                raise BackupError(f"{path.name}: quick_check: {result}")
            report: dict[str, str | int] = {
                "version": conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
            }
            for table in TABLES:
                report[table] = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    except sqlite3.Error as e:
        raise BackupError(f"{path.name}: {e}") from e
    return report


def backup(db_path: Path, backup_dir: Path, now: datetime) -> Path:
    """Writes a checked snapshot of `db_path` into `backup_dir` and returns its path."""
    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / snapshot_name(now)
    if target.exists():
        raise BackupError(f"{target.name} already exists")
    # A plain sqlite3 connection in autocommit mode: VACUUM can't run inside a transaction, and
    # the app's engine starts every transaction with BEGIN IMMEDIATE.
    conn = sqlite3.connect(db_path, timeout=30, isolation_level=None)
    try:
        conn.execute("VACUUM INTO ?", (str(target),))
    except sqlite3.Error as e:
        target.unlink(missing_ok=True)
        raise BackupError(f"VACUUM INTO failed: {e}") from e
    finally:
        conn.close()
    try:
        verify(target)
    except BackupError:
        target.unlink(missing_ok=True)  # a damaged snapshot must never count as a backup
        raise
    return target


def rotate(backup_dir: Path, now: datetime) -> list[str]:
    """Deletes expired snapshots; returns their names, oldest first."""
    deleted = []
    for path in sorted(Path(backup_dir).iterdir()):
        match = _NAME.fullmatch(path.name)
        if not match:
            continue  # not ours: never touched
        taken = datetime.strptime(match.group(1), _FORMAT).replace(tzinfo=UTC)
        age = now - taken
        if age <= KEEP_ALL or (taken.hour == 0 and age <= KEEP_MIDNIGHT):
            continue
        path.unlink()
        deleted.append(path.name)
    return deleted
