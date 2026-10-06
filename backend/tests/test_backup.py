"""On-server backups, layer 1 (PROJECT_PLAN.md §3 Backups). Written before swipe_rss/backup.py.

A snapshot is a consistent copy of the live database (VACUUM INTO, never a plain file copy),
checked before it counts. Rotation keeps every snapshot of the last 48 hours plus the midnight
snapshot of each day for 14 days, and never touches files it didn't name.

The check opens the snapshot as a separate, read-only database and runs real queries on it; the
production database is only ever read, by the backup itself.
"""

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from swipe_rss.backup import BackupError, backup, rotate, snapshot_name, verify
from swipe_rss.models import Item

NOW = datetime(2026, 10, 6, 15, 37, tzinfo=UTC)


def add_items(engine, n: int) -> None:
    with Session(engine) as s, s.begin():
        for i in range(n):
            s.add(Item(feed_id="a", dedup_key=f"guid:{i}", headline=f"h{i}", summary="", fetched_at=NOW))


def rows(path, table: str) -> int:
    with sqlite3.connect(path) as conn:
        return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def test_snapshot_names_carry_the_utc_time():
    assert snapshot_name(NOW) == "swipe_rss-20261006T1537Z.db"


def test_backup_writes_a_complete_snapshot(engine, db_path, tmp_path):
    add_items(engine, 3)
    target = backup(db_path, tmp_path / "backups", NOW)
    assert target == tmp_path / "backups" / snapshot_name(NOW)
    assert rows(target, "items") == 3
    with Session(engine) as s:
        assert s.scalar(select(func.count()).select_from(Item)) == 3  # the live database is untouched


def test_backup_includes_writes_still_in_the_wal_file(engine, db_path, tmp_path):
    # The reason for VACUUM INTO: in WAL mode recent writes live in swipe_rss.db-wal until a
    # checkpoint; a plain copy of swipe_rss.db would miss them.
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA wal_autocheckpoint = 0")
    add_items(engine, 2)
    assert (db_path.parent / (db_path.name + "-wal")).stat().st_size > 0
    target = backup(db_path, tmp_path / "backups", NOW)
    assert rows(target, "items") == 2


def test_backup_keeps_the_schema_including_the_swipe_triggers(engine, db_path, tmp_path):
    target = backup(db_path, tmp_path / "backups", NOW)
    with sqlite3.connect(target) as conn:
        triggers = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")}
        version = conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    assert {"swipes_no_delete", "swipes_no_update"} <= triggers
    assert version == "0004"


def test_backup_never_overwrites_an_existing_snapshot(engine, db_path, tmp_path):
    backup(db_path, tmp_path, NOW)
    with pytest.raises(BackupError):
        backup(db_path, tmp_path, NOW)


def test_verify_queries_the_snapshot_as_a_separate_database(engine, db_path, tmp_path):
    add_items(engine, 3)
    target = backup(db_path, tmp_path / "backups", NOW)
    # A change to production after the snapshot must not show up in the check.
    with Session(engine) as s, s.begin():
        s.add(Item(feed_id="z", dedup_key="guid:after", headline="after", summary="", fetched_at=NOW))
    report = verify(target)
    assert report == {
        "version": "0004",
        "feed_status": 0,
        "items": 3,
        "tombstones": 0,
        "swipes": 0,
        "saved": 0,
    }


def test_verify_rejects_a_damaged_file(tmp_path):
    damaged = tmp_path / "damaged.db"
    damaged.write_bytes(b"SQLite format 3\x00" + b"\x00" * 100 + b"garbage" * 200)
    with pytest.raises(BackupError):
        verify(damaged)


def make_snapshots(directory, *ages: timedelta) -> None:
    directory.mkdir(exist_ok=True)
    for age in ages:
        (directory / snapshot_name(NOW - age)).write_bytes(b"x")


def test_rotation_keeps_everything_from_the_last_48_hours(tmp_path):
    make_snapshots(tmp_path, timedelta(hours=1), timedelta(hours=47), timedelta(hours=48))
    assert rotate(tmp_path, NOW) == []
    assert len(list(tmp_path.iterdir())) == 3


def test_rotation_deletes_older_snapshots_except_midnight_ones(tmp_path):
    # NOW is 15:37, so NOW - 39h is 00:37 two days ago (a midnight snapshot), NOW - 49h is 14:37.
    make_snapshots(tmp_path, timedelta(hours=49), timedelta(hours=63))
    assert rotate(tmp_path, NOW) == [snapshot_name(NOW - timedelta(hours=49))]
    assert [p.name for p in tmp_path.iterdir()] == [snapshot_name(NOW - timedelta(hours=63))]


def test_rotation_keeps_midnight_snapshots_for_14_days(tmp_path):
    midnight_14_days = NOW.replace(hour=0) - timedelta(days=13)  # within 14 days
    midnight_15_days = NOW.replace(hour=0) - timedelta(days=15)
    make_snapshots(tmp_path, NOW - midnight_14_days, NOW - midnight_15_days)
    assert rotate(tmp_path, NOW) == [snapshot_name(midnight_15_days)]


def test_rotation_never_touches_files_it_did_not_name(tmp_path):
    make_snapshots(tmp_path, timedelta(days=30))
    for name in ("notes.txt", "swipe_rss-manual-copy.db", "swipe_rss-20200101T0000Z.db.keep"):
        (tmp_path / name).write_text("keep me")
    rotate(tmp_path, NOW)
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "notes.txt",
        "swipe_rss-20200101T0000Z.db.keep",
        "swipe_rss-manual-copy.db",
    ]
