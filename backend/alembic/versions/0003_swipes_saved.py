"""swipes, saved, items.swiped_at

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-03

swipes: permanent swipe log with the card snapshot (PROJECT_PLAN.md §3 Swipe log fields).
saved: read-later entries, one per article, doubling as the extraction job queue.
items.swiped_at: a swipe flags its item instead of deleting it.

Timestamps are ISO-8601 UTC TEXT (see swipe_rss.models.UTCDateTime).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Plain ALTER TABLE ADD COLUMN (no batch recreate), so items stays STRICT.
    op.add_column("items", sa.Column("swiped_at", sa.Text(), nullable=True))

    op.create_table(
        "swipes",
        sa.Column("swipe_id", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("swiped_at", sa.Text(), nullable=False),
        sa.Column("received_at", sa.Text(), nullable=False),
        sa.Column("tz_offset_minutes", sa.Integer(), nullable=False),
        sa.Column("time_to_swipe_ms", sa.Integer(), nullable=True),
        sa.Column("app_version", sa.Integer(), nullable=False),
        sa.Column("feed_id", sa.Text(), nullable=False),
        sa.Column("item_key", sa.Text(), nullable=False),
        sa.Column("headline", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("link", sa.Text(), nullable=True),
        sa.Column("published_at", sa.Text(), nullable=True),
        sa.Column("fetched_at", sa.Text(), nullable=False),
        sa.Column("author", sa.Text(), nullable=True),
        sa.Column("tags", sa.Text(), nullable=False),
        sa.CheckConstraint("action IN ('never', 'save', 'read_now')", name="ck_swipes_action"),
        sa.PrimaryKeyConstraint("swipe_id"),
        sqlite_strict=True,
    )
    op.create_index("ix_swipes_item", "swipes", ["feed_id", "item_key"])

    op.create_table(
        "saved",
        sa.Column("feed_id", sa.Text(), nullable=False),
        sa.Column("item_key", sa.Text(), nullable=False),
        sa.Column("swipe_id", sa.Text(), nullable=False),
        sa.Column("extraction_status", sa.Text(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("next_attempt_at", sa.Text(), nullable=True),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("extracted_at", sa.Text(), nullable=True),
        sa.Column("read_at", sa.Text(), nullable=True),
        sa.CheckConstraint("extraction_status IN ('pending', 'done', 'failed')", name="ck_saved_extraction_status"),
        sa.ForeignKeyConstraint(["swipe_id"], ["swipes.swipe_id"]),
        sa.PrimaryKeyConstraint("feed_id", "item_key"),
        sqlite_strict=True,
    )


def downgrade() -> None:
    op.drop_table("saved")
    op.drop_index("ix_swipes_item", table_name="swipes")
    op.drop_table("swipes")
    op.drop_column("items", "swiped_at")
