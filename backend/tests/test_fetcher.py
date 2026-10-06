import asyncio
import json
import threading
import time
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
from annotated_types import MaxLen
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from swipe_rss import fetcher
from swipe_rss.api_models import Card, Tag
from swipe_rss.dedup import dedup_key
from swipe_rss.feeds import Feed, FeedsFile
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


def _max_len(model, field: str) -> int:
    return next(m.max_length for m in model.model_fields[field].metadata if isinstance(m, MaxLen))


def test_ingest_limits_fit_the_api_card():
    # Every stored item must be a valid Card, or a swipe on it is rejected forever.
    assert _max_len(Card, "headline") >= fetcher.HEADLINE_MAX_CHARS
    assert _max_len(Card, "summary") >= fetcher.SUMMARY_MAX_CHARS
    assert _max_len(Card, "author") >= fetcher.AUTHOR_MAX_CHARS
    assert _max_len(Card, "link") >= fetcher.LINK_MAX_CHARS
    assert _max_len(Card, "tags") >= fetcher.TAGS_MAX
    assert _max_len(Tag, "root") >= fetcher.TAG_MAX_CHARS


HUGE = {
    "title": "T" * 5000,
    "link": "https://a.example/" + "p" * 5000,
    "guid": "huge",
    "published": NOW,
    "description": "S" * 5000,
    "author": "A" * 5000,
    "categories": [f"tag{i}-" + "c" * 500 for i in range(80)],
}


def test_oversized_entry_is_capped_to_a_valid_card():
    feed = Feed(id="a", url="https://a.example/feed")
    [item] = fetcher.parse_entries(feed, rss(HUGE), {})

    assert len(item.headline) == fetcher.HEADLINE_MAX_CHARS and item.headline.endswith("…")
    assert len(item.summary) == fetcher.SUMMARY_MAX_CHARS
    assert len(item.author) == fetcher.AUTHOR_MAX_CHARS
    assert item.link is None  # dropped, not truncated
    assert len(item.tags) == fetcher.TAGS_MAX and all(len(t) == fetcher.TAG_MAX_CHARS for t in item.tags)
    Card.model_validate(
        {
            "feed_id": feed.id,
            "item_key": item.dedup_key,
            "headline": item.headline,
            "summary": item.summary,
            "link": item.link,
            "published_at": item.published_at,
            "fetched_at": NOW,
            "author": item.author,
            "tags": item.tags,
        }
    )


def test_dropped_link_still_keys_the_item():
    # Identity must not depend on the card limit: the key comes from the full link.
    long_link = "https://a.example/" + "p" * 5000
    feed = Feed(id="a", url="https://a.example/feed")
    [item] = fetcher.parse_entries(feed, rss({"title": "t", "link": long_link}), {})
    assert item.link is None
    assert item.dedup_key == dedup_key(guid=None, link=long_link, title="t", published=None)


def test_redirects_are_followed(engine):
    def handler(request):
        if request.url.path == "/feed":
            return httpx.Response(301, headers={"Location": "https://a.example/moved"})
        return httpx.Response(200, content=rss(RECENT))

    summary = run(engine, feeds_file("a"), handler)
    assert summary.failed == 0 and summary.new_items == 1


def test_requests_identify_the_reader(engine):
    seen = []

    def handler(request):
        seen.append(request.headers.get("user-agent"))
        return httpx.Response(200, content=rss(RECENT))

    run(engine, feeds_file("a"), handler)
    assert seen == [fetcher.USER_AGENT]


def test_missing_or_blank_author_is_none():
    feed = Feed(id="a", url="https://a.example/feed")
    items = fetcher.parse_entries(feed, rss({"title": "no author"}, {"title": "blank", "author": "   "}), {})
    assert [i.author for i in items] == [None, None]


def test_updated_date_is_used_when_there_is_no_published_date():
    atom = (
        b'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>t</title><id>f</id>'
        b"<updated>2026-10-03T06:00:00Z</updated><entry><title>Only updated</title><id>u1</id>"
        b'<link href="https://a.example/u"/><updated>2026-10-03T06:00:00Z</updated></entry></feed>'
    )
    [item] = fetcher.parse_entries(Feed(id="a", url="https://a.example/feed"), atom, {})
    assert item.published_at == datetime(2026, 10, 3, 6, 0, tzinfo=UTC)


@pytest.mark.parametrize("date", ["3000-01-01T00:00:00Z", "1969-12-31T23:59:59Z"])
def test_date_outside_the_api_range_becomes_null(date):
    # Otherwise the card couldn't be swiped: the API rejects such timestamps (422).
    atom = (
        b'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>t</title><id>f</id>'
        b'<entry><title>Odd date</title><id>d1</id><link href="https://a.example/d"/>'
        b"<published>" + date.encode() + b"</published></entry></feed>"
    )
    [item] = fetcher.parse_entries(Feed(id="a", url="https://a.example/feed"), atom, {})
    assert item.published_at is None


def test_entry_without_title_element_is_skipped_and_later_entries_kept():
    # feedparser omits the "title" key entirely when there is no <title> element.
    body = (
        b'<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>'
        b"<item><link>https://a.example/no-title</link><guid>nt</guid></item>"
        b"<item><title>After</title><guid>g2</guid></item></channel></rss>"
    )
    items = fetcher.parse_entries(Feed(id="a", url="https://a.example/feed"), body, {})
    assert [i.headline for i in items] == ["After"]


def test_new_entry_after_an_already_seen_one_is_stored(engine):
    run(engine, feeds_file("a"), lambda req: httpx.Response(200, content=rss(RECENT)))
    newer = {**RECENT, "guid": "g2", "link": "https://a.example/newer", "title": "Newer"}
    summary = run(engine, feeds_file("a"), lambda req: httpx.Response(200, content=rss(RECENT, newer)))
    assert summary.new_items == 1
    assert count(engine, Item) == 2


def test_author_and_tags_are_stored(engine):
    entry = {**RECENT, "author": "Jane Doe", "categories": ["privacy", "crypto"]}
    run(engine, feeds_file("a"), lambda req: httpx.Response(200, content=rss(entry)))
    with Session(engine) as s:
        item = s.scalars(select(Item)).one()
    assert item.author == "Jane Doe"
    assert json.loads(item.tags) == ["privacy", "crypto"]


def test_response_in_several_chunks_is_read_whole(engine):
    body = rss(RECENT, UNDATED)

    async def chunks():
        for i in range(0, len(body), 64):
            yield body[i : i + 64]

    summary = run(engine, feeds_file("a"), lambda req: httpx.Response(200, content=chunks()))
    assert summary.failed == 0 and summary.new_items == 2


def test_charset_from_the_http_header_is_used(engine):
    # Without the response headers feedparser guesses the encoding and stores mojibake.
    body = rss({"title": "שלום עולם", "guid": "he1"}).decode().encode("windows-1255")
    headers = {"Content-Type": "application/rss+xml; charset=windows-1255"}
    run(engine, feeds_file("a"), lambda req: httpx.Response(200, content=body, headers=headers))
    with Session(engine) as s:
        assert s.scalars(select(Item.headline)).all() == ["שלום עולם"]


def test_unexpected_error_in_one_feed_is_recorded_and_others_continue(engine, monkeypatch):
    real_parse = fetcher.parse_entries

    def parse(feed, body, headers):
        if feed.id == "bad":
            raise RuntimeError("boom")
        return real_parse(feed, body, headers)

    monkeypatch.setattr(fetcher, "parse_entries", parse)
    summary = run(engine, feeds_file("bad", "good"), lambda req: httpx.Response(200, content=rss(RECENT)))

    assert summary.failed == 1 and summary.new_items == 1
    bad = status(engine, "bad")
    assert bad.last_error == "RuntimeError: boom" and bad.consecutive_failures == 1


def test_entry_without_guid_or_link_is_keyed_by_title_and_date():
    feed = Feed(id="a", url="https://a.example/feed")
    [item] = fetcher.parse_entries(feed, rss({"title": "T", "published": NOW}), {})
    assert item.dedup_key == dedup_key(guid=None, link=None, title="T", published=format_datetime(NOW))


def test_dedup_link_override_ignores_the_guid():
    feed = Feed(id="a", url="https://a.example/feed", dedup="link")
    [item] = fetcher.parse_entries(feed, rss({"title": "T", "guid": "g", "link": "https://a.example/x"}), {})
    assert item.dedup_key == dedup_key(guid="g", link="https://a.example/x", title="T", published=None, mode="link")
    assert item.dedup_key.startswith("link:")


def test_last_modified_is_stored_and_sent_back(engine):
    lm = "Mon, 05 Oct 2026 10:00:00 GMT"
    seen = []

    def handler(request):
        seen.append(request.headers.get("if-modified-since"))
        if request.headers.get("if-modified-since") == lm:
            return httpx.Response(304)
        return httpx.Response(200, content=rss(RECENT), headers={"Last-Modified": lm})

    for _ in range(3):  # 200, then 304 twice: a 304 must keep the stored value
        run(engine, feeds_file("a"), handler)
    assert seen == [None, lm, lm]
    assert status(engine, "a").last_modified == lm


def test_feed_status_after_failure_and_recovery(engine):
    responses = iter([httpx.Response(500), httpx.Response(200, content=rss(RECENT))])
    run(engine, feeds_file("a"), lambda req: next(responses))
    failed = status(engine, "a")
    assert failed.last_attempt_at is not None and failed.consecutive_failures == 1

    run(engine, feeds_file("a"), lambda req: next(responses))
    ok = status(engine, "a")
    assert ok.consecutive_failures == 0 and ok.last_error is None
    assert ok.last_attempt_at >= failed.last_attempt_at and ok.last_success_at == ok.last_attempt_at


class _HangingHandler(BaseHTTPRequestHandler):
    release = threading.Event()

    def do_GET(self):
        self.release.wait(3)  # far longer than the patched timeout; released at teardown
        self.send_response(200)
        self.end_headers()
        self.wfile.write(rss(RECENT))

    def log_message(self, *args):
        pass


def test_hanging_feed_times_out(engine, monkeypatch):
    # Without a timeout one hanging server would stall the whole run, and with it all fetching.
    monkeypatch.setattr(fetcher, "TIMEOUT", httpx.Timeout(0.3))
    _HangingHandler.release.clear()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _HangingHandler)
    httpd.daemon_threads, httpd.block_on_close = True, False
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    ff = FeedsFile.model_validate({"feeds": [{"id": "slow", "url": f"http://127.0.0.1:{httpd.server_port}/feed"}]})
    try:
        start = time.monotonic()
        summary = asyncio.run(fetcher.run_fetch(engine, ff))
        elapsed = time.monotonic() - start
    finally:
        _HangingHandler.release.set()
        httpd.shutdown()
        httpd.server_close()
    assert summary.failed == 1 and elapsed < 2
    assert "Timeout" in status(engine, "slow").last_error
