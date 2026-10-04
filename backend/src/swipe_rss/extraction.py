"""Extraction job: fetch saved articles and store their text (PROJECT_PLAN.md §3 Content extraction).

Run every minute by supercronic (`swipe-rss extract`). The `saved` table is the queue:

1. Claim (one short transaction): up to CLAIM_BATCH `pending` rows that are due
   (`next_attempt_at` empty or past). Each claimed row gets `attempts + 1` and a lease
   (`next_attempt_at = now + LEASE`), so an overlapping run skips it. Counting the attempt at
   claim time means a URL that crashes the job still uses up its attempts.
2. Fetch and extract each row outside any transaction, through the SSRF guard.
3. Store the result (one short transaction per row):
   - text found: `done`.
   - temporary failure: stays `pending`, retried after BACKOFF[attempts - 1].
   - permanent failure, or the MAX_ATTEMPTS-th failure: `failed`.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import trafilatura
from sqlalchemy import Engine, or_, select
from sqlalchemy.orm import Session

from swipe_rss.models import Saved, Swipe
from swipe_rss.safe_fetch import FetchError, Page, fetch_html
from swipe_rss.text import truncate

log = logging.getLogger(__name__)

CLAIM_BATCH = 5
LEASE = timedelta(minutes=10)
MAX_ATTEMPTS = 3
BACKOFF = [timedelta(minutes=5), timedelta(minutes=30)]  # after the 1st and 2nd failure
TEXT_MAX_CHARS = 500_000
ERROR_MAX_CHARS = 1000


@dataclass(frozen=True)
class Job:
    feed_id: str
    item_key: str
    link: str | None
    attempts: int  # including this one


@dataclass
class ExtractionSummary:
    claimed: int = 0
    done: int = 0
    retrying: int = 0
    failed: int = 0


def run_extraction(
    engine: Engine,
    *,
    fetch: Callable[[str], Page] = fetch_html,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> ExtractionSummary:
    jobs = _claim(engine, now())
    summary = ExtractionSummary(claimed=len(jobs))
    for job in jobs:
        try:
            text = _extract(job, fetch)
        except FetchError as e:
            failed = _store_failure(engine, job, str(e), permanent=e.permanent, now=now())
        except Exception as e:  # one bad page must not stop the run; treated as temporary
            log.exception("extraction crashed: feed=%s item=%s", job.feed_id, job.item_key)
            failed = _store_failure(engine, job, f"{type(e).__name__}: {e}", permanent=False, now=now())
        else:
            _store_done(engine, job, text, now())
            summary.done += 1
            continue
        if failed:
            summary.failed += 1
        else:
            summary.retrying += 1
    return summary


def _claim(engine: Engine, now: datetime) -> list[Job]:
    with Session(engine) as session, session.begin():
        rows = session.execute(
            select(Saved, Swipe.link)
            .join(Swipe, Saved.swipe_id == Swipe.swipe_id)
            .where(Saved.extraction_status == "pending")
            .where(or_(Saved.next_attempt_at.is_(None), Saved.next_attempt_at <= now))
            .order_by(Swipe.swiped_at)
            .limit(CLAIM_BATCH)
        ).all()
        jobs = []
        for saved, link in rows:
            saved.attempts += 1
            saved.next_attempt_at = now + LEASE
            jobs.append(Job(saved.feed_id, saved.item_key, link, saved.attempts))
        return jobs


def _extract(job: Job, fetch: Callable[[str], Page]) -> str:
    if not job.link:
        raise FetchError("saved item has no link", permanent=True)
    page = fetch(job.link)
    text = trafilatura.extract(page.body, url=page.url, include_comments=False)
    if not text:
        raise FetchError("no article text found on the page", permanent=True)
    return truncate(text, TEXT_MAX_CHARS)


def _store_done(engine: Engine, job: Job, text: str, now: datetime) -> None:
    with Session(engine) as session, session.begin():
        saved = session.get(Saved, (job.feed_id, job.item_key))
        saved.extraction_status = "done"
        saved.text = text
        saved.extracted_at = now
        saved.last_error = None
        saved.next_attempt_at = None


def _store_failure(engine: Engine, job: Job, error: str, *, permanent: bool, now: datetime) -> bool:
    """Record a failed attempt. Returns True if the entry is now `failed` for good."""
    give_up = permanent or job.attempts >= MAX_ATTEMPTS
    with Session(engine) as session, session.begin():
        saved = session.get(Saved, (job.feed_id, job.item_key))
        saved.last_error = error[:ERROR_MAX_CHARS]
        if give_up:
            saved.extraction_status = "failed"
            saved.next_attempt_at = None
        else:
            saved.next_attempt_at = now + BACKOFF[job.attempts - 1]
    log.warning(
        "extraction %s: feed=%s item=%s attempt=%d error=%s",
        "failed" if give_up else "will retry", job.feed_id, job.item_key, job.attempts, error,
    )  # fmt: skip
    return give_up
