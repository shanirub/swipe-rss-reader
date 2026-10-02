"""Alembic environment: uses the app engine (WAL, busy_timeout, BEGIN IMMEDIATE)."""

from logging.config import fileConfig

from alembic import context

from swipe_rss.config import get_settings
from swipe_rss.db import make_engine
from swipe_rss.models import Base

config = context.config
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations() -> None:
    # Tests pass their own engine; otherwise use the configured database.
    engine = config.attributes.get("engine") or make_engine(get_settings().db_path)
    with engine.connect() as connection:
        # Batch mode: SQLite can't ALTER most things, so Alembic recreates tables.
        context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    raise SystemExit("offline mode is not supported")
run_migrations()
