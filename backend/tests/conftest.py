from datetime import datetime
from email.utils import format_datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from swipe_rss.db import make_engine

BACKEND_DIR = Path(__file__).resolve().parents[1]


def migrate(engine) -> None:
    cfg = Config(BACKEND_DIR / "alembic.ini")
    cfg.attributes["engine"] = engine
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "head")


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "test.db"


@pytest.fixture
def engine(db_path):
    engine = make_engine(db_path)
    migrate(engine)
    yield engine
    engine.dispose()


def rss(*entries: dict) -> bytes:
    """Minimal RSS 2.0 document. Entry keys: title, link, guid, published (datetime), description."""
    items = []
    for e in entries:
        parts = [f"<title>{e['title']}</title>"]
        if "link" in e:
            parts.append(f"<link>{e['link']}</link>")
        if "guid" in e:
            parts.append(f'<guid isPermaLink="false">{e["guid"]}</guid>')
        if "published" in e:
            published: datetime = e["published"]
            parts.append(f"<pubDate>{format_datetime(published)}</pubDate>")
        if "description" in e:
            parts.append(f"<description><![CDATA[{e['description']}]]></description>")
        items.append(f"<item>{''.join(parts)}</item>")
    return (
        '<?xml version="1.0"?><rss version="2.0"><channel><title>t</title><link>https://example.com/</link>'
        f"<description>d</description>{''.join(items)}</channel></rss>"
    ).encode()
