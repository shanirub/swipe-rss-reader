import json

from alembic import command
from alembic.config import Config
from sqlalchemy import text

from swipe_rss.db import make_engine

from .conftest import BACKEND_DIR


def _upgrade(engine, revision: str) -> None:
    cfg = Config(BACKEND_DIR / "alembic.ini")
    cfg.attributes["engine"] = engine
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, revision)


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
