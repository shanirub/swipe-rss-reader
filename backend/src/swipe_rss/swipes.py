"""Recording swipes (PROJECT_PLAN.md §3 Swipe recording).

One transaction per batch: every swipe is inserted with ON CONFLICT(swipe_id) DO NOTHING,
so a resent batch is harmless (idempotent). A swipe flags its item as swiped (it leaves
the queue) if the item still exists, and a newly stored `save` swipe creates the article's
saved entry unless one exists. Swipes for items that are already gone are stored all the
same: the card snapshot is all they need.
"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from swipe_rss.api_models import Action, Swipe
from swipe_rss.models import Item, Saved
from swipe_rss.models import Swipe as SwipeRow


@dataclass(frozen=True)
class BatchResult:
    stored: int
    duplicates: int


def record_swipes(session: Session, swipes: list[Swipe], now: datetime | None = None) -> BatchResult:
    now = now or datetime.now(UTC)
    stored = 0
    for swipe in swipes:
        card = swipe.card
        inserted = session.execute(
            insert(SwipeRow)
            .values(
                swipe_id=str(swipe.swipe_id),
                action=swipe.action.value,
                swiped_at=swipe.swiped_at,
                received_at=now,
                tz_offset_minutes=swipe.tz_offset_minutes,
                time_to_swipe_ms=swipe.time_to_swipe_ms,
                app_version=swipe.app_version,
                feed_id=card.feed_id,
                item_key=card.item_key,
                headline=card.headline,
                summary=card.summary,
                link=card.link,
                published_at=card.published_at,
                fetched_at=card.fetched_at,
                author=card.author,
                tags=json.dumps([tag.root for tag in card.tags], ensure_ascii=False),
            )
            .on_conflict_do_nothing(index_elements=["swipe_id"])
        ).rowcount
        if not inserted:
            continue  # already stored by an earlier (re)send
        stored += 1
        session.execute(
            update(Item)
            .where(Item.feed_id == card.feed_id, Item.dedup_key == card.item_key, Item.swiped_at.is_(None))
            .values(swiped_at=now)
        )
        if swipe.action == Action.save:
            session.execute(
                insert(Saved)
                .values(
                    feed_id=card.feed_id,
                    item_key=card.item_key,
                    swipe_id=str(swipe.swipe_id),
                    extraction_status="pending",
                    attempts=0,
                )
                .on_conflict_do_nothing(index_elements=["feed_id", "item_key"])
            )
    return BatchResult(stored=stored, duplicates=len(swipes) - stored)
