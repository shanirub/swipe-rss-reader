"""Command-line entry point: `swipe-rss fetch`, `swipe-rss extract`."""

import argparse
import asyncio
import logging

from swipe_rss.config import get_settings
from swipe_rss.db import make_engine
from swipe_rss.extraction import run_extraction
from swipe_rss.feeds import FeedsFileError, load_feeds
from swipe_rss.fetcher import run_fetch

log = logging.getLogger("swipe_rss")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="swipe-rss")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("fetch", help="fetch all feeds once and store new items")
    commands.add_parser("extract", help="extract the text of due saved articles")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    settings = get_settings()

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
    return 0
