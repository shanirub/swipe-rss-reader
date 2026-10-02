import asyncio
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from swipe_rss import fetcher
from swipe_rss.feeds import FeedsFile
from swipe_rss.models import FeedStatus, Item, Tombstone

from .conftest import rss

NOW = datetime.now(UTC)


def feeds_file(*ids: str, max_age: int | None = 24) -> FeedsFile:
    return FeedsFile.model_validate(
        {
            "defaults": {"max_item_age_hours": max_age},
            "feeds": [{"id": i, "url": f"https://{i}.example/feed"} for i in ids],
        }
    )


def run(engine, ff: FeedsFile, handler) -> fetcher.RunSummary:
    return asyncio.run(fetcher.run_fetch(engine, ff, transport=httpx.MockTransport(handler)))


def count(engine, model) -> int:
    with Session(engine) as s:
        return s.scalar(select(func.count()).select_from(model))


def status(engine, feed_id) -> FeedStatus:
    with Session(engine) as s:
        return s.get(FeedStatus, feed_id)


RECENT = {
    "title": "Recent <b>news</b>",
    "link": "https://A.example/post?utm_source=rss#top",
    "guid": "g1",
    "published": NOW - timedelta(hours=1),
    "description": "<p>Hello &amp; <i>welcome</i></p>",
}
OLD = {"title": "Old", "link": "https://a.example/old", "guid": "g0", "published": NOW - timedelta(hours=48)}
UNDATED = {"title": "Undated", "link": "https://a.example/undated"}
UNTITLED = {"title": "", "link": "https://a.example/untitled", "guid": "g9"}


def test_ingest_filters_and_normalizes(engine):
    body = rss(RECENT, OLD, UNDATED, UNTITLED)
    summary = run(engine, feeds_file("a"), lambda req: httpx.Response(200, content=body))

    assert summary.new_items == 2  # RECENT + UNDATED; OLD is past 24h, UNTITLED has no headline
    with Session(engine) as s:
        item = s.scalars(select(Item).where(Item.headline == "Recent news")).one()
    assert item.link == "https://a.example/post"
    assert item.summary == "Hello & welcome"
    assert item.dedup_key.startswith("guid:")
    assert item.published_at is not None and item.published_at.tzinfo is not None
    assert count(engine, Tombstone) == 2  # age-filtered entries are not tombstoned
    st = status(engine, "a")
    assert st.last_success_at and st.last_new_item_at and st.last_error is None


def test_no_age_limit_keeps_old_items(engine):
    run(engine, feeds_file("a", max_age=None), lambda req: httpx.Response(200, content=rss(OLD)))
    assert count(engine, Item) == 1


def test_rerun_inserts_nothing(engine):
    body = rss(RECENT, UNDATED)
    run(engine, feeds_file("a"), lambda req: httpx.Response(200, content=body))
    summary = run(engine, feeds_file("a"), lambda req: httpx.Response(200, content=body))
    assert summary.new_items == 0
    assert count(engine, Item) == 2


def test_tombstones_block_reinsert_after_pruning(engine):
    body = rss(RECENT)
    run(engine, feeds_file("a"), lambda req: httpx.Response(200, content=body))
    with Session(engine) as s, s.begin():
        s.execute(delete(Item))
    run(engine, feeds_file("a"), lambda req: httpx.Response(200, content=body))
    assert count(engine, Item) == 0


def test_first_version_wins(engine):
    run(engine, feeds_file("a"), lambda req: httpx.Response(200, content=rss(RECENT)))
    edited = {**RECENT, "title": "Edited headline"}
    run(engine, feeds_file("a"), lambda req: httpx.Response(200, content=rss(edited)))
    with Session(engine) as s:
        assert s.scalars(select(Item.headline)).all() == ["Recent news"]


def test_same_article_in_two_feeds_is_kept_twice(engine):
    body = rss(RECENT)
    run(engine, feeds_file("a", "b"), lambda req: httpx.Response(200, content=body))
    assert count(engine, Item) == 2


def test_conditional_get(engine):
    seen_headers = []

    def handler(request):
        seen_headers.append(request.headers.get("if-none-match"))
        if request.headers.get("if-none-match") == '"v1"':
            return httpx.Response(304)
        return httpx.Response(200, content=rss(RECENT), headers={"ETag": '"v1"'})

    run(engine, feeds_file("a"), handler)
    first_success = status(engine, "a").last_success_at
    summary = run(engine, feeds_file("a"), handler)

    assert seen_headers == [None, '"v1"']
    assert summary.not_modified == 1
    st = status(engine, "a")
    assert st.etag == '"v1"' and st.last_success_at >= first_success


def test_failed_feed_is_recorded_and_others_continue(engine):
    def handler(request):
        if request.url.host == "bad.example":
            return httpx.Response(500)
        return httpx.Response(200, content=rss(RECENT))

    run(engine, feeds_file("bad", "good"), handler)
    summary = run(engine, feeds_file("bad", "good"), handler)

    assert summary.failed == 1
    bad = status(engine, "bad")
    assert bad.consecutive_failures == 2 and "500" in bad.last_error and bad.last_success_at is None
    assert count(engine, Item) == 1


def test_unparseable_feed_is_an_error(engine):
    summary = run(engine, feeds_file("a"), lambda req: httpx.Response(200, content=b"<html>not a feed"))
    assert summary.failed == 1
    assert "FeedError" in status(engine, "a").last_error


def test_oversized_response_is_an_error(engine, monkeypatch):
    monkeypatch.setattr(fetcher, "MAX_FEED_BYTES", 100)
    summary = run(engine, feeds_file("a"), lambda req: httpx.Response(200, content=rss(RECENT, UNDATED)))
    assert summary.failed == 1
    assert count(engine, Item) == 0
