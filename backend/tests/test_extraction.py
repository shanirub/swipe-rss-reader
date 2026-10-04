"""The extraction job (swipe_rss/extraction.py), with a fake fetch and a controllable clock."""

import logging
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from swipe_rss import extraction
from swipe_rss.extraction import BACKOFF, LEASE, run_extraction
from swipe_rss.models import Saved, Swipe
from swipe_rss.safe_fetch import FetchError, Page

T0 = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
ARTICLE = (
    b"<html><body><article><h1>Title</h1><p>"
    + b"An actual article paragraph with enough words to count as content. " * 20
    + b"</p></article></body></html>"
)


def add_saved(engine, key: str, link: str | None = "https://news.example/a", minutes_ago: int = 0) -> None:
    with Session(engine) as s, s.begin():
        s.add(
            Swipe(
                swipe_id=f"swipe-{key}", action="save", swiped_at=T0 - timedelta(minutes=minutes_ago), received_at=T0,
                tz_offset_minutes=0, app_version=1, feed_id="a", item_key=key, headline="h", summary="",
                link=link, fetched_at=T0, tags="[]",
            )
        )  # fmt: skip
        s.flush()
        s.add(Saved(feed_id="a", item_key=key, swipe_id=f"swipe-{key}", extraction_status="pending", attempts=0))


def saved(engine, key: str = "k1") -> Saved:
    with Session(engine) as s:
        return s.get(Saved, ("a", key))


def page(body: bytes = ARTICLE):
    return lambda url: Page(url=url, body=body)


def failing(message: str, permanent: bool):
    def fetch(url):
        raise FetchError(message, permanent=permanent)

    return fetch


def run(engine, fetch, at: datetime = T0):
    return run_extraction(engine, fetch=fetch, now=lambda: at)


def test_done(engine):
    add_saved(engine, "k1", link="https://news.example/story")
    asked = []

    def fetch(url):
        asked.append(url)
        return Page(url=url, body=ARTICLE)

    summary = run(engine, fetch)
    assert asked == ["https://news.example/story"]
    row = saved(engine)
    assert (summary.claimed, summary.done) == (1, 1)
    assert row.extraction_status == "done" and "actual article paragraph" in row.text
    assert row.extracted_at == T0 and row.attempts == 1 and row.next_attempt_at is None and row.last_error is None


def test_temporary_failures_retry_with_backoff_then_fail(engine):
    add_saved(engine, "k1")
    flaky = failing("HTTP 503", permanent=False)

    run(engine, flaky, T0)
    first = saved(engine)
    assert (first.extraction_status, first.attempts, first.last_error) == ("pending", 1, "HTTP 503")
    assert first.next_attempt_at == T0 + BACKOFF[0]

    assert run(engine, flaky, T0 + BACKOFF[0] - timedelta(seconds=1)).claimed == 0  # not due yet

    t2 = T0 + BACKOFF[0]
    run(engine, flaky, t2)
    second = saved(engine)
    assert (second.extraction_status, second.attempts, second.next_attempt_at) == ("pending", 2, t2 + BACKOFF[1])

    run(engine, flaky, t2 + BACKOFF[1])
    third = saved(engine)
    assert (third.extraction_status, third.attempts, third.next_attempt_at) == ("failed", 3, None)


def test_permanent_failure_gives_up_at_once(engine):
    add_saved(engine, "k1")
    summary = run(engine, failing("HTTP 404", permanent=True))
    row = saved(engine)
    assert summary.failed == 1 and (row.extraction_status, row.attempts, row.last_error) == ("failed", 1, "HTTP 404")


def test_no_link_fails_without_fetching(engine):
    add_saved(engine, "k1", link=None)

    def must_not_fetch(url):
        raise AssertionError("fetched although there is no link")

    run(engine, must_not_fetch)
    assert saved(engine).extraction_status == "failed" and saved(engine).last_error == "saved item has no link"


def test_page_without_article_text_fails(engine):
    add_saved(engine, "k1")
    run(engine, page(b"<html><body></body></html>"))
    assert (saved(engine).extraction_status, saved(engine).last_error) == (
        "failed",
        "no article text found on the page",
    )


def test_crash_counts_as_a_temporary_failure_and_is_logged(engine, caplog):
    add_saved(engine, "k1")

    def crash(url):
        raise RuntimeError("boom")

    with caplog.at_level(logging.ERROR, logger="swipe_rss.extraction"):
        summary = run(engine, crash)
    row = saved(engine)
    assert summary.retrying == 1 and row.extraction_status == "pending" and row.last_error == "RuntimeError: boom"
    assert any("extraction crashed" in r.getMessage() for r in caplog.records)


def test_claimed_rows_are_leased(engine):
    # A second run during the lease must not pick the same row (e.g. overlapping cron runs).
    add_saved(engine, "k1")
    extraction._claim(engine, T0)
    row = saved(engine)
    assert row.attempts == 1 and row.next_attempt_at == T0 + LEASE  # attempt counted at claim time
    assert run(engine, page(), T0 + LEASE - timedelta(seconds=1)).claimed == 0
    assert run(engine, page(), T0 + LEASE).claimed == 1  # lease expired (crashed run): picked up again


def test_batch_is_limited_and_oldest_save_first(engine, monkeypatch):
    monkeypatch.setattr(extraction, "CLAIM_BATCH", 2)
    for i, minutes_ago in enumerate([10, 30, 20]):
        add_saved(engine, f"k{i}", minutes_ago=minutes_ago)
    jobs = extraction._claim(engine, T0)
    assert [j.item_key for j in jobs] == ["k1", "k2"]  # 30 and 20 minutes ago


@pytest.mark.parametrize("status", ["done", "failed"])
def test_finished_entries_are_not_claimed(engine, status):
    add_saved(engine, "k1")
    with Session(engine) as s, s.begin():
        s.scalars(select(Saved)).one().extraction_status = status
    assert run(engine, page()).claimed == 0


def test_very_long_text_is_capped(engine, monkeypatch):
    monkeypatch.setattr(extraction, "TEXT_MAX_CHARS", 50)
    add_saved(engine, "k1")
    run(engine, page())
    assert len(saved(engine).text) == 50


def test_every_claimed_row_is_processed_and_counted(engine):
    for key in ("k1", "k2", "k3"):
        add_saved(engine, key)
    summary = run(engine, page())
    assert (summary.claimed, summary.done) == (3, 3)
    assert {saved(engine, k).extraction_status for k in ("k1", "k2", "k3")} == {"done"}


def test_failures_are_counted_per_row(engine):
    for key in ("k1", "k2"):
        add_saved(engine, key)
    assert run(engine, failing("HTTP 503", permanent=False)).retrying == 2
    for key in ("k3", "k4"):
        add_saved(engine, key)
    assert run(engine, failing("HTTP 404", permanent=True)).failed == 2
