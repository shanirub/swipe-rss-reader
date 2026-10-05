"""Logging setup shared by the jobs (cli.py) and the API (api.py).

SWIPE_RSS_LOG_LEVEL (default INFO) sets the level of the app's own loggers and uvicorn's.
Size on disk is limited separately, by Docker log rotation in compose.yaml.
"""

import logging

FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
LOGGERS = ("swipe_rss", "uvicorn", "uvicorn.error", "uvicorn.access")


class SkipHealthChecks(logging.Filter):
    """Drops uvicorn access-log lines for /health: Docker's health check calls it every 30 s,
    which made 99.8% of the API's log (measured 2026-10-05)."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args if isinstance(record.args, tuple) else ()
        # uvicorn.access args: (client, method, path with query, http version, status)
        return not (len(args) >= 3 and str(args[2]).split("?")[0] == "/health")


def configure_logging(level: str) -> None:
    """Raises ValueError for an unknown level name, so a typo fails at startup."""
    logging.basicConfig(format=FORMAT)  # no-op if the root logger already has handlers
    for name in LOGGERS:
        logging.getLogger(name).setLevel(level)
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, SkipHealthChecks) for f in access.filters):
        access.addFilter(SkipHealthChecks())
