"""Fetch run: feeds.toml → conditional GET → parse → age filter → dedup → store.

Network I/O never happens inside a transaction: each feed is fetched and parsed
first, then its results are written in one short transaction.
"""

import asyncio
import calendar
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import feedparser
import httpx
from sqlalchemy import Engine, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from swipe_rss.dedup import dedup_key, normalize_link
from swipe_rss.feeds import Feed, FeedsFile
from swipe_rss.models import FeedStatus, Item, Tombstone
from swipe_rss.text import html_to_text

log = logging.getLogger(__name__)

USER_AGENT = "swipe-rss-reader/0.1 (+https://github.com/shanirub/swipe-rss-reader)"
TIMEOUT = httpx.Timeout(20.0)
MAX_FEED_BYTES = 10 * 1024 * 1024
CONCURRENCY = 5
SUMMARY_MAX_CHARS = 2000
ERROR_MAX_CHARS = 1000


class FeedError(Exception):
    pass


@dataclass(frozen=True)
class ParsedItem:
    dedup_key: str
    headline: str
    summary: str
    link: str | None
    published_at: datetime | None
    author: str | None
    tags: list[str]


@dataclass
class FetchResult:
    feed_id: str
    items: list[ParsedItem] = field(default_factory=list)
    not_modified: bool = False
    etag: str | None = None
    last_modified: str | None = None
    error: str | None = None


@dataclass
class RunSummary:
    feeds: int = 0
    failed: int = 0
    not_modified: int = 0
    new_items: int = 0


def _entry_time(entry) -> datetime | None:
    # feedparser normalizes *_parsed to UTC struct_time.
    st = entry.get("published_parsed") or entry.get("updated_parsed")
    return datetime.fromtimestamp(calendar.timegm(st), UTC) if st else None


def parse_entries(feed: Feed, body: bytes, response_headers: dict[str, str]) -> list[ParsedItem]:
    parsed = feedparser.parse(body, response_headers=response_headers)
    if parsed.bozo and not parsed.entries:
        raise FeedError(f"unparseable feed: {parsed.get('bozo_exception')!r}")

    items = []
    for entry in parsed.entries:
        headline = html_to_text(entry.get("title", ""))
        if not headline:
            continue  # nothing to show on a card
        raw_link = entry.get("link")
        link = normalize_link(raw_link) if raw_link else None
        summary_html = entry.get("summary") or (entry.get("content") or [{}])[0].get("value", "")
        items.append(
            ParsedItem(
                dedup_key=dedup_key(
                    guid=entry.get("id"),
                    link=link,
                    title=headline,
                    published=entry.get("published") or entry.get("updated"),
                    mode=feed.dedup,
                ),
                headline=headline,
                summary=html_to_text(summary_html, max_chars=SUMMARY_MAX_CHARS),
                link=link,
                published_at=_entry_time(entry),
                author=entry.get("author") or None,
                tags=[t["term"] for t in entry.get("tags", []) if t.get("term")],
            )
        )
    return items


async def fetch_feed(client: httpx.AsyncClient, feed: Feed, etag: str | None, last_modified: str | None) -> FetchResult:
    headers = {}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    try:
        async with client.stream("GET", feed.url, headers=headers) as response:
            if response.status_code == 304:
                return FetchResult(feed.id, not_modified=True, etag=etag, last_modified=last_modified)
            response.raise_for_status()
            body = bytearray()
            async for chunk in response.aiter_bytes():  # decoded, so the cap also bounds decompression
                body += chunk
                if len(body) > MAX_FEED_BYTES:
                    raise FeedError(f"response larger than {MAX_FEED_BYTES} bytes")
            response_headers = dict(response.headers)
        return FetchResult(
            feed.id,
            items=parse_entries(feed, bytes(body), response_headers),
            etag=response_headers.get("etag"),
            last_modified=response_headers.get("last-modified"),
        )
    except (httpx.HTTPError, FeedError) as e:
        log.warning("fetch failed: feed=%s url=%s error=%r", feed.id, feed.url, e)
        return FetchResult(feed.id, error=f"{type(e).__name__}: {e}")
    except Exception as e:
        # One broken feed must not abort the run; unexpected errors get a traceback.
        log.exception("fetch failed unexpectedly: feed=%s url=%s", feed.id, feed.url)
        return FetchResult(feed.id, error=f"{type(e).__name__}: {e}")


def store_result(engine: Engine, result: FetchResult, now: datetime, max_age_hours: int | None) -> int:
    """Write one feed's results in a single transaction. Returns the number of new items."""
    cutoff = now - timedelta(hours=max_age_hours) if max_age_hours else None
    with Session(engine) as session, session.begin():
        status = session.get(FeedStatus, result.feed_id)
        if status is None:
            status = FeedStatus(feed_id=result.feed_id, consecutive_failures=0)
            session.add(status)
        status.last_attempt_at = now

        if result.error:
            status.last_error = result.error[:ERROR_MAX_CHARS]
            status.consecutive_failures += 1
            return 0

        status.last_success_at = now
        status.last_error = None
        status.consecutive_failures = 0
        status.etag = result.etag
        status.last_modified = result.last_modified

        new = 0
        for item in result.items:
            if cutoff and (item.published_at or now) < cutoff:
                continue  # too old; not tombstoned, so it is simply skipped again next run
            seen = insert(Tombstone).values(feed_id=result.feed_id, dedup_key=item.dedup_key, first_seen_at=now)
            if session.execute(seen.on_conflict_do_nothing()).rowcount == 0:
                continue  # first version wins
            session.add(
                Item(
                    feed_id=result.feed_id,
                    dedup_key=item.dedup_key,
                    headline=item.headline,
                    summary=item.summary,
                    link=item.link,
                    published_at=item.published_at,
                    fetched_at=now,
                    author=item.author,
                    tags=json.dumps(item.tags, ensure_ascii=False) if item.tags else None,
                )
            )
            new += 1
        if new:
            status.last_new_item_at = now
        return new


def _load_validators(engine: Engine) -> dict[str, tuple[str | None, str | None]]:
    with Session(engine) as session, session.begin():
        rows = session.execute(select(FeedStatus.feed_id, FeedStatus.etag, FeedStatus.last_modified))
        return {feed_id: (etag, lm) for feed_id, etag, lm in rows}


async def run_fetch(
    engine: Engine, feeds_file: FeedsFile, *, transport: httpx.AsyncBaseTransport | None = None
) -> RunSummary:
    validators = _load_validators(engine)
    max_age = feeds_file.defaults.max_item_age_hours
    summary = RunSummary(feeds=len(feeds_file.feeds))
    semaphore = asyncio.Semaphore(CONCURRENCY)

    async with httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT, follow_redirects=True, transport=transport
    ) as client:

        async def fetch_one(feed: Feed) -> FetchResult:
            async with semaphore:
                return await fetch_feed(client, feed, *validators.get(feed.id, (None, None)))

        for next_done in asyncio.as_completed([fetch_one(f) for f in feeds_file.feeds]):
            result = await next_done
            # Sync write on the event loop: one short transaction, acceptable at this scale.
            new = store_result(engine, result, datetime.now(UTC), max_age)
            summary.new_items += new
            if result.error:
                summary.failed += 1
            elif result.not_modified:
                summary.not_modified += 1
            log.info("feed=%s new=%d%s", result.feed_id, new, " (304)" if result.not_modified else "")
    return summary
