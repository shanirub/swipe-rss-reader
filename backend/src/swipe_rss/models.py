"""ORM models. All tables are STRICT, so only Integer/Text column types are used."""

from datetime import UTC, datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, Text, TypeDecorator, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

STRICT = {"sqlite_strict": True}


class UTCDateTime(TypeDecorator):
    """Timezone-aware datetime stored as ISO-8601 UTC text (sortable, readable in sqlite3)."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> str | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime; use timezone-aware UTC")
        return value.astimezone(UTC).isoformat(timespec="seconds")

    def process_result_value(self, value: str | None, dialect) -> datetime | None:
        return None if value is None else datetime.fromisoformat(value)


class Base(DeclarativeBase):
    pass


class FeedStatus(Base):
    """Per-feed fetch state, keyed by the stable feed id from feeds.toml."""

    __tablename__ = "feed_status"
    __table_args__ = (STRICT,)

    feed_id: Mapped[str] = mapped_column(Text, primary_key=True)
    etag: Mapped[str | None] = mapped_column(Text)
    last_modified: Mapped[str | None] = mapped_column(Text)
    last_attempt_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_success_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_new_item_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_error: Mapped[str | None] = mapped_column(Text)
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class Item(Base):
    """A fetched article, subject to retention."""

    __tablename__ = "items"
    __table_args__ = (UniqueConstraint("feed_id", "dedup_key", name="uq_items_feed_dedup_key"), STRICT)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    feed_id: Mapped[str] = mapped_column(Text, nullable=False)
    dedup_key: Mapped[str] = mapped_column(Text, nullable=False)
    headline: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)  # plain text
    link: Mapped[str | None] = mapped_column(Text)  # normalized
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    fetched_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    author: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[str | None] = mapped_column(Text)  # JSON array
    # Set when swiped: the item leaves the queue but stays until pruned (keeps undo possible).
    swiped_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class Tombstone(Base):
    """Every dedup key ever seen, written at first sight. Dedup checks only this table."""

    __tablename__ = "tombstones"
    __table_args__ = (STRICT,)

    feed_id: Mapped[str] = mapped_column(Text, primary_key=True)
    dedup_key: Mapped[str] = mapped_column(Text, primary_key=True)
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)


class Swipe(Base):
    """Permanent, append-only swipe log: the training labels (PROJECT_PLAN.md §3 Swipe log fields).

    Stores the card as displayed, so a swipe stays meaningful after its item is pruned.
    Several swipes may exist per item; for training the latest per (feed_id, item_key) wins.
    """

    __tablename__ = "swipes"
    __table_args__ = (
        CheckConstraint("action IN ('never', 'save', 'read_now')", name="ck_swipes_action"),
        Index("ix_swipes_item", "feed_id", "item_key"),
        STRICT,
    )

    swipe_id: Mapped[str] = mapped_column(Text, primary_key=True)  # UUID from the phone; idempotency key
    action: Mapped[str] = mapped_column(Text, nullable=False)
    swiped_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)  # phone clock
    received_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)  # server clock
    tz_offset_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    time_to_swipe_ms: Mapped[int | None] = mapped_column(Integer)
    app_version: Mapped[int] = mapped_column(Integer, nullable=False)
    # Card snapshot
    feed_id: Mapped[str] = mapped_column(Text, nullable=False)
    item_key: Mapped[str] = mapped_column(Text, nullable=False)
    headline: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    link: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    fetched_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    author: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[str] = mapped_column(Text, nullable=False)  # JSON array, possibly empty


class Saved(Base):
    """Read-later entry, one per article; also the extraction job queue.

    Display fields come from the save swipe (join on swipe_id), not copies.
    """

    __tablename__ = "saved"
    __table_args__ = (
        CheckConstraint("extraction_status IN ('pending', 'done', 'failed')", name="ck_saved_extraction_status"),
        STRICT,
    )

    feed_id: Mapped[str] = mapped_column(Text, primary_key=True)
    item_key: Mapped[str] = mapped_column(Text, primary_key=True)
    swipe_id: Mapped[str] = mapped_column(Text, ForeignKey("swipes.swipe_id"), nullable=False)
    extraction_status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    next_attempt_at: Mapped[datetime | None] = mapped_column(UTCDateTime)  # NULL = due now
    text: Mapped[str | None] = mapped_column(Text)
    extracted_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    read_at: Mapped[datetime | None] = mapped_column(UTCDateTime)  # stage 6
