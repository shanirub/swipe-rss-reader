"""The swipe queue: unswiped items, round-robin across feeds (PROJECT_PLAN.md §3 Queue ordering).

Each feed's unswiped items are ranked oldest first (published_at, or fetched_at when the
feed gave no date). The queue takes every feed's first item, then every feed's second
item, and so on; within one round, older items come first. Stateless: the same cards are
returned until they are swiped.
"""

import json
import logging

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from swipe_rss.api_models import Card
from swipe_rss.models import Item

log = logging.getLogger(__name__)


def select_queue(session: Session, limit: int) -> list[Card]:
    age = func.coalesce(Item.published_at, Item.fetched_at)
    ranked = (
        select(
            Item,
            age.label("age"),
            func.row_number().over(partition_by=Item.feed_id, order_by=(age, Item.id)).label("rank"),
        )
        .where(Item.swiped_at.is_(None))
        .subquery()
    )
    item = select(Item).join(ranked, Item.id == ranked.c.id).order_by(ranked.c.rank, ranked.c.age, Item.id).limit(limit)
    cards = []
    for row in session.scalars(item):
        try:
            cards.append(_card(row))
        except ValidationError as e:
            # Ingest guarantees valid cards; if a row slips through anyway, skip it instead of
            # failing the whole queue (one bad row must not block every card).
            log.error("item %s is not a valid API card, skipped: %s", row.id, e)
    return cards


def _card(item: Item) -> Card:
    return Card(
        feed_id=item.feed_id,
        item_key=item.dedup_key,
        headline=item.headline,
        summary=item.summary,
        link=item.link,
        published_at=item.published_at,
        fetched_at=item.fetched_at,
        author=item.author,
        tags=json.loads(item.tags) if item.tags else [],
    )
