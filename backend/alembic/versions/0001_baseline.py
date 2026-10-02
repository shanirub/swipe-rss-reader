"""baseline: feed_status, items, tombstones

Revision ID: 0001
Revises:
Create Date: 2026-10-02

Timestamps are ISO-8601 UTC TEXT (see swipe_rss.models.UTCDateTime).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "feed_status",
        sa.Column("feed_id", sa.Text(), nullable=False),
        sa.Column("etag", sa.Text(), nullable=True),
        sa.Column("last_modified", sa.Text(), nullable=True),
        sa.Column("last_attempt_at", sa.Text(), nullable=True),
        sa.Column("last_success_at", sa.Text(), nullable=True),
        sa.Column("last_new_item_at", sa.Text(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("feed_id"),
        sqlite_strict=True,
    )
    op.create_table(
        "items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("feed_id", sa.Text(), nullable=False),
        sa.Column("dedup_key", sa.Text(), nullable=False),
        sa.Column("headline", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("link", sa.Text(), nullable=True),
        sa.Column("published_at", sa.Text(), nullable=True),
        sa.Column("fetched_at", sa.Text(), nullable=False),
        sa.Column("author", sa.Text(), nullable=True),
        sa.Column("tags", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("feed_id", "dedup_key", name="uq_items_feed_dedup_key"),
        sqlite_strict=True,
    )
    op.create_table(
        "tombstones",
        sa.Column("feed_id", sa.Text(), nullable=False),
        sa.Column("dedup_key", sa.Text(), nullable=False),
        sa.Column("first_seen_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("feed_id", "dedup_key"),
        sqlite_strict=True,
    )


def downgrade() -> None:
    op.drop_table("tombstones")
    op.drop_table("items")
    op.drop_table("feed_status")
