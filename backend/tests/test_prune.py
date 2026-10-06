"""Retention (PROJECT_PLAN.md §3 Retention). Written before swipe_rss/prune.py (test-first).

Rules: items are deleted 2 days after `fetched_at`, swiped or not; saved entries 2 weeks after
the save swipe's `received_at`, read or not; swipes, tombstones and feed status are never deleted.
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from swipe_rss.models import FeedStatus, Item, Saved, Swipe, Tombstone
from swipe_rss.prune import ITEM_MAX_AGE, SAVED_MAX_AGE, PruneResult, prune

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)


def add_item(engine, key: str, *, fetched: timedelta, swiped: timedelta | None = None, published=None) -> None:
    with Session(engine) as s, s.begin():
        s.add(
            Item(
                feed_id="a",
                dedup_key=f"guid:{key}",
                headline=key,
                summary="",
                fetched_at=NOW - fetched,
                swiped_at=None if swiped is None else NOW - swiped,
                published_at=published,
            )
        )


def add_save(engine, key: str, *, received: timedelta, swiped: timedelta | None = None, read: bool = False) -> str:
    swipe_id = str(uuid.uuid4())
    with Session(engine) as s, s.begin():
        s.add(
            Swipe(
                swipe_id=swipe_id,
                action="save",
                swiped_at=NOW - (swiped if swiped is not None else received),
                received_at=NOW - received,
                tz_offset_minutes=0,
                app_version=1,
                feed_id="a",
                item_key=f"guid:{key}",
                headline=key,
                summary="",
                fetched_at=NOW - received,
                tags="[]",
            )
        )
        s.flush()
        s.add(Saved(feed_id="a", item_key=f"guid:{key}", swipe_id=swipe_id, read_at=NOW if read else None))
    return swipe_id


def item_keys(engine) -> set[str]:
    with Session(engine) as s:
        return set(s.scalars(select(Item.headline)))


def saved_keys(engine) -> set[str]:
    with Session(engine) as s:
        return {k.removeprefix("guid:") for k in s.scalars(select(Saved.item_key))}


def count(engine, model) -> int:
    with Session(engine) as s:
        return s.scalar(select(func.count()).select_from(model))


SECOND = timedelta(seconds=1)


def test_the_limits_are_the_decided_ones():
    assert timedelta(days=2) == ITEM_MAX_AGE
    assert timedelta(weeks=2) == SAVED_MAX_AGE


def test_items_are_deleted_two_days_after_fetch(engine):
    add_item(engine, "fresh", fetched=timedelta(days=1))
    add_item(engine, "at-limit", fetched=ITEM_MAX_AGE)  # exactly 2 days: kept
    add_item(engine, "expired", fetched=ITEM_MAX_AGE + SECOND)
    prune(engine, NOW)
    assert item_keys(engine) == {"fresh", "at-limit"}


def test_swiped_items_follow_the_same_rule(engine):
    add_item(engine, "swiped-recent-fetch", fetched=timedelta(days=1), swiped=timedelta(days=1))
    add_item(engine, "swiped-old-fetch", fetched=timedelta(days=3), swiped=timedelta(minutes=1))
    prune(engine, NOW)
    assert item_keys(engine) == {"swiped-recent-fetch"}


def test_the_publish_date_does_not_matter(engine):
    add_item(engine, "old-article-new-fetch", fetched=timedelta(hours=1), published=NOW - timedelta(days=30))
    prune(engine, NOW)
    assert item_keys(engine) == {"old-article-new-fetch"}


def test_saved_entries_are_deleted_two_weeks_after_the_save_is_received(engine):
    add_save(engine, "fresh", received=timedelta(days=13))
    add_save(engine, "at-limit", received=SAVED_MAX_AGE)  # exactly 2 weeks: kept
    add_save(engine, "expired", received=SAVED_MAX_AGE + SECOND)
    prune(engine, NOW)
    assert saved_keys(engine) == {"fresh", "at-limit"}


def test_the_server_clock_counts_not_the_phone_clock(engine):
    # A swipe made offline weeks ago but synced yesterday: the 2 weeks start at the sync.
    add_save(engine, "late-sync", received=timedelta(days=1), swiped=timedelta(days=20))
    prune(engine, NOW)
    assert saved_keys(engine) == {"late-sync"}


def test_read_and_unread_saved_entries_are_treated_alike(engine):
    add_save(engine, "read-fresh", received=timedelta(days=1), read=True)
    add_save(engine, "read-expired", received=timedelta(days=15), read=True)
    add_save(engine, "unread-expired", received=timedelta(days=15))
    prune(engine, NOW)
    assert saved_keys(engine) == {"read-fresh"}


def test_swipes_tombstones_and_feed_status_are_never_deleted(engine):
    for i in range(3):
        add_save(engine, f"ancient-{i}", received=timedelta(days=400))
    add_item(engine, "ancient-item", fetched=timedelta(days=400))
    with Session(engine) as s, s.begin():
        s.add(Tombstone(feed_id="a", dedup_key="guid:ancient-item", first_seen_at=NOW - timedelta(days=400)))
        s.add(FeedStatus(feed_id="a", consecutive_failures=0, last_success_at=NOW - timedelta(days=400)))

    prune(engine, NOW)

    assert count(engine, Item) == 0 and count(engine, Saved) == 0  # the job did run
    assert count(engine, Swipe) == 3
    assert count(engine, Tombstone) == 1
    assert count(engine, FeedStatus) == 1


def test_the_result_counts_what_was_deleted(engine):
    add_item(engine, "expired-1", fetched=timedelta(days=3))
    add_item(engine, "expired-2", fetched=timedelta(days=3))
    add_item(engine, "fresh", fetched=timedelta(hours=1))
    add_save(engine, "expired", received=timedelta(days=15))
    assert prune(engine, NOW) == PruneResult(items=2, saved=1)
    assert prune(engine, NOW) == PruneResult(items=0, saved=0)  # nothing left to do


def test_dry_run_counts_without_deleting(engine):
    add_item(engine, "expired", fetched=timedelta(days=3))
    add_save(engine, "expired", received=timedelta(days=15))

    assert prune(engine, NOW, dry_run=True) == PruneResult(items=1, saved=1)
    assert count(engine, Item) == 1 and count(engine, Saved) == 1

    assert prune(engine, NOW) == PruneResult(items=1, saved=1)
    assert count(engine, Item) == 0 and count(engine, Saved) == 0
