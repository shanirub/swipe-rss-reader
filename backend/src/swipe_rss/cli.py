"""Command-line entry point: `swipe-rss fetch`, `swipe-rss extract`, `swipe-rss prune`."""

import argparse
import asyncio
import logging
from datetime import UTC, datetime

from swipe_rss.config import get_settings
from swipe_rss.db import make_engine
from swipe_rss.extraction import run_extraction
from swipe_rss.feeds import FeedsFileError, load_feeds
from swipe_rss.fetcher import run_fetch
from swipe_rss.logs import configure_logging
from swipe_rss.prune import prune

log = logging.getLogger("swipe_rss")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="swipe-rss")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("fetch", help="fetch all feeds once and store new items")
    commands.add_parser("extract", help="extract the text of due saved articles")
    prune_cmd = commands.add_parser("prune", help="delete expired items and saved entries (never swipes)")
    prune_cmd.add_argument("--dry-run", action="store_true", help="only count what would be deleted")
    args = parser.parse_args(argv)

    settings = get_settings()
    configure_logging(settings.log_level)  # SWIPE_RSS_LOG_LEVEL; third-party loggers stay at WARNING

    if args.command == "fetch":
        try:
            feeds = load_feeds(settings.feeds_path)
        except FeedsFileError as e:
            log.error("aborting fetch run, invalid feeds file: %s", e)
            return 2
        summary = asyncio.run(run_fetch(make_engine(settings.db_path), feeds))
        log.info(
            "fetch run done: feeds=%d failed=%d not_modified=%d new_items=%d",
            summary.feeds, summary.failed, summary.not_modified, summary.new_items,
        )  # fmt: skip
    elif args.command == "extract":
        summary = run_extraction(make_engine(settings.db_path))
        if summary.claimed:
            log.info(
                "extraction run done: claimed=%d done=%d retrying=%d failed=%d",
                summary.claimed, summary.done, summary.retrying, summary.failed,
            )  # fmt: skip
    elif args.command == "prune":
        result = prune(make_engine(settings.db_path), datetime.now(UTC), dry_run=args.dry_run)
        log.info(
            "prune run done%s: items=%d saved=%d",
            " (dry run, nothing deleted)" if args.dry_run else "", result.items, result.saved,
        )  # fmt: skip
    return 0
