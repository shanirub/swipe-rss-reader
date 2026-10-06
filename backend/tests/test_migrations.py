import json

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from swipe_rss.db import make_engine
from swipe_rss.models import Base

from .conftest import BACKEND_DIR


def _config(engine) -> Config:
    cfg = Config(BACKEND_DIR / "alembic.ini")
    cfg.attributes["engine"] = engine
    cfg.attributes["configure_logger"] = False
    return cfg


def _upgrade(engine, revision: str) -> None:
    command.upgrade(_config(engine), revision)


def test_models_match_migrations(engine):
    # models.py and the migrations must describe the same schema, or code expects columns no migration creates.
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []


def test_downgrade_and_upgrade_again(engine):
    command.downgrade(_config(engine), "0002")
    with engine.connect() as conn:
        tables = {r[0] for r in conn.exec_driver_sql("SELECT name FROM sqlite_schema WHERE type = 'table'")}
    assert not {"swipes", "saved"} & tables
    _upgrade(engine, "head")


SWIPE = {
    "swipe_id": "s1",
    "action": "save",
    "swiped_at": "2026-10-03T08:00:00+00:00",
    "received_at": "2026-10-03T08:00:01+00:00",
    "tz_offset_minutes": 180,
    "app_version": 1,
    "feed_id": "a",
    "item_key": "guid:x",
    "headline": "h",
    "summary": "",
    "fetched_at": "2026-10-03T07:00:00+00:00",
    "tags": "[]",
}
INSERT_SWIPE = text(
    "INSERT INTO swipes (swipe_id, action, swiped_at, received_at, tz_offset_minutes, app_version,"
    " feed_id, item_key, headline, summary, fetched_at, tags) VALUES (:swipe_id, :action, :swiped_at,"
    " :received_at, :tz_offset_minutes, :app_version, :feed_id, :item_key, :headline, :summary, :fetched_at, :tags)"
)
INSERT_SAVED = text(
    "INSERT INTO saved (feed_id, item_key, swipe_id, extraction_status, attempts)"
    " VALUES ('a', 'guid:x', :swipe_id, :status, 0)"
)


def test_0003_swipe_and_saved_rows(engine):
    with engine.begin() as conn:
        conn.execute(INSERT_SWIPE, SWIPE)
        conn.execute(INSERT_SAVED, {"swipe_id": "s1", "status": "pending"})
    with engine.connect() as conn:
        assert conn.execute(text("SELECT s.headline FROM saved JOIN swipes s USING (swipe_id)")).scalar() == "h"


@pytest.mark.parametrize(
    ("statement", "params"),
    [
        (
            INSERT_SWIPE,
            {**SWIPE, "swipe_id": "s2", "action": "maybe"},
        ),  # CHECK: unknown action (new id, not a PK clash)
        (INSERT_SAVED, {"swipe_id": "no-such-swipe", "status": "pending"}),  # FK: saved needs its swipe
        (INSERT_SAVED, {"swipe_id": "s1", "status": "unknown"}),  # CHECK: unknown extraction status
    ],
)
def test_0003_constraints_reject_bad_rows(engine, statement, params):
    with engine.begin() as conn:
        conn.execute(INSERT_SWIPE, SWIPE)
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(statement, params)


def test_0002_caps_items_stored_before_the_limits(db_path):
    engine = make_engine(db_path)
    _upgrade(engine, "0001")
    insert = text(
        "INSERT INTO items (feed_id, dedup_key, headline, summary, link, published_at, fetched_at, author, tags)"
        " VALUES (:feed_id, :key, :headline, '', :link, NULL, '2026-10-01T00:00:00+00:00', :author, :tags)"
    )
    with engine.begin() as conn:
        conn.execute(
            insert,
            {
                "feed_id": "a",
                "key": "guid:big",
                "headline": "T" * 5000,
                "link": "https://a.example/" + "p" * 5000,
                "author": "A" * 5000,
                "tags": json.dumps(["c" * 500] * 80),
            },
        )
        conn.execute(
            insert,
            {
                "feed_id": "a",
                "key": "guid:ok",
                "headline": "fine",
                "link": "https://a.example/ok",
                "author": None,
                "tags": None,
            },
        )

    _upgrade(engine, "head")

    with engine.connect() as conn:
        big, ok = conn.execute(text("SELECT headline, link, author, tags FROM items ORDER BY dedup_key")).all()
    assert len(big.headline) == 1000 and big.headline.endswith("…")
    assert big.link is None
    assert len(big.author) == 500
    tags = json.loads(big.tags)
    assert len(tags) == 50 and all(len(t) == 200 for t in tags)
    assert tuple(ok) == ("fine", "https://a.example/ok", None, None)  # untouched
    engine.dispose()


@pytest.mark.parametrize(
    "statement",
    ["DELETE FROM swipes", "DELETE FROM swipes WHERE swipe_id = 's1'", "UPDATE swipes SET headline = 'changed'"],
)
def test_0004_swipes_can_never_be_deleted_or_changed(engine, statement):
    # Checked at head on purpose: a later batch-mode migration that rebuilds `swipes` would drop
    # the triggers silently (SQLite triggers don't survive copy-drop-rename), and this test would fail.
    with engine.begin() as conn:
        conn.execute(INSERT_SWIPE, SWIPE)
    with pytest.raises(IntegrityError, match="append-only"), engine.begin() as conn:
        conn.execute(text(statement))
    with engine.connect() as conn:
        assert conn.execute(text("SELECT headline FROM swipes")).scalars().all() == ["h"]


def test_0004_swipes_still_accept_inserts(engine):
    with engine.begin() as conn:
        conn.execute(INSERT_SWIPE, SWIPE)
        conn.execute(INSERT_SWIPE, {**SWIPE, "swipe_id": "s2"})
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM swipes")).scalar() == 2


def test_0004_downgrade_removes_the_triggers(engine):
    command.downgrade(_config(engine), "0003")
    with engine.begin() as conn:
        conn.execute(INSERT_SWIPE, SWIPE)
        conn.execute(text("DELETE FROM swipes"))
    _upgrade(engine, "head")
