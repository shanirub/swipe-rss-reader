"""The read-later list (PROJECT_PLAN.md §3 Content extraction).

A saved entry's display fields come from its save swipe (join on swipe_id), so the list
works even after the item itself was pruned. Newest save first.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from swipe_rss.api_models import ExtractionStatus, SavedContent, SavedEntry
from swipe_rss.models import Saved, Swipe


def list_saved(session: Session) -> list[SavedEntry]:
    rows = session.execute(
        select(Saved, Swipe).join(Swipe, Saved.swipe_id == Swipe.swipe_id).order_by(Swipe.swiped_at.desc())
    )
    return [
        SavedEntry(
            feed_id=saved.feed_id,
            item_key=saved.item_key,
            headline=swipe.headline,
            summary=swipe.summary,
            link=swipe.link,
            published_at=swipe.published_at,
            saved_at=swipe.swiped_at,
            extraction_status=ExtractionStatus(saved.extraction_status),
            read_at=saved.read_at,
        )
        for saved, swipe in rows
    ]


def saved_content(session: Session, feed_id: str, item_key: str) -> SavedContent | None:
    saved = session.get(Saved, (feed_id, item_key))
    if saved is None:
        return None
    return SavedContent(
        extraction_status=ExtractionStatus(saved.extraction_status),
        text=saved.text if saved.extraction_status == "done" else None,
        extracted_at=saved.extracted_at,
        last_error=saved.last_error,
    )
