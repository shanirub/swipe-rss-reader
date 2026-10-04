"""Per-feed health for GET /feeds (PROJECT_PLAN.md §3 Observability).

One entry per feed currently in feeds.toml, in file order, joined with its fetch state.
A feed that was never fetched has null timestamps and zero failures. Feeds removed from
feeds.toml are not listed, even if they still have a feed_status row.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from swipe_rss.api_models import FeedStatus as FeedStatusOut
from swipe_rss.feeds import FeedsFile
from swipe_rss.models import FeedStatus


def feed_health(session: Session, feeds_file: FeedsFile) -> list[FeedStatusOut]:
    states = {s.feed_id: s for s in session.scalars(select(FeedStatus))}
    result = []
    for feed in feeds_file.feeds:
        state = states.get(feed.id)
        result.append(
            FeedStatusOut(
                feed_id=feed.id,
                name=feed.name,
                url=feed.url,
                last_attempt_at=state.last_attempt_at if state else None,
                last_success_at=state.last_success_at if state else None,
                last_new_item_at=state.last_new_item_at if state else None,
                last_error=state.last_error if state else None,
                consecutive_failures=state.consecutive_failures if state else 0,
            )
        )
    return result
