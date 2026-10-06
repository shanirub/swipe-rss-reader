"""Retention: deletes expired items and saved entries (PROJECT_PLAN.md §3 Retention).

- items: 2 days after `fetched_at`, swiped or not (one rule; undo can clear the flag until then)
- saved: 2 weeks after the save swipe's `received_at` (server clock), read or not
- swipes, tombstones, feed_status: never (swipes are also protected by triggers, migration 0004)

One transaction per run; at steady state that is a handful of rows. `now` is passed in.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import Engine, delete, func, select
from sqlalchemy.orm import Session

from swipe_rss.models import Item, Saved, Swipe

ITEM_MAX_AGE = timedelta(days=2)
SAVED_MAX_AGE = timedelta(weeks=2)


@dataclass(frozen=True)
class PruneResult:
    items: int
    saved: int


def prune(engine: Engine, now: datetime, *, dry_run: bool = False) -> PruneResult:
    """Deletes what has expired at `now`; with `dry_run`, only counts it."""
    expired_item = Item.fetched_at < now - ITEM_MAX_AGE
    expired_save = Saved.swipe_id.in_(select(Swipe.swipe_id).where(Swipe.received_at < now - SAVED_MAX_AGE))
    with Session(engine) as session, session.begin():
        if dry_run:
            return PruneResult(
                items=session.scalar(select(func.count()).select_from(Item).where(expired_item)),
                saved=session.scalar(select(func.count()).select_from(Saved).where(expired_save)),
            )
        items = session.execute(delete(Item).where(expired_item)).rowcount
        saved = session.execute(delete(Saved).where(expired_save)).rowcount
    return PruneResult(items=items, saved=saved)
