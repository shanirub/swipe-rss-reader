"""Logging setup (swipe_rss/logs.py) and the SWIPE_RSS_LOG_LEVEL setting."""

import logging

import pytest

from swipe_rss.config import get_settings
from swipe_rss.logs import LOGGERS, SkipHealthChecks, configure_logging


@pytest.fixture(autouse=True)
def restore_levels():
    loggers = [logging.getLogger(name) for name in LOGGERS]
    levels = [logger.level for logger in loggers]
    yield
    for logger, level in zip(loggers, levels, strict=True):
        logger.setLevel(level)


def access_record(path: str) -> logging.LogRecord:
    # The shape uvicorn.access uses: (client, method, path with query, http version, status)
    return logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
                             ("127.0.0.1:5000", "GET", path, "1.1", 200), None)  # fmt: skip


@pytest.mark.parametrize("level", ["DEBUG", "WARNING", "ERROR"])
def test_level_applies_to_the_app_and_uvicorn(level):
    configure_logging(level)
    assert {logging.getLogger(name).level for name in LOGGERS} == {logging.getLevelName(level)}


def test_unknown_level_fails_at_startup():
    with pytest.raises(ValueError):
        configure_logging("VERBOSE")


def test_level_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("SWIPE_RSS_LOG_LEVEL", "debug")
    assert get_settings().log_level == "DEBUG"
    monkeypatch.delenv("SWIPE_RSS_LOG_LEVEL")
    assert get_settings().log_level == "INFO"


@pytest.mark.parametrize(("path", "kept"), [("/health", False), ("/health?probe=1", False), ("/queue", True),
                                             ("/healthz", True)])  # fmt: skip
def test_health_checks_are_dropped_from_the_access_log(path, kept):
    assert SkipHealthChecks().filter(access_record(path)) is kept


def test_other_records_pass_the_health_filter():
    record = logging.LogRecord("uvicorn.error", logging.INFO, "", 0, "Started server process [%d]", (1,), None)
    assert SkipHealthChecks().filter(record) is True


def test_health_filter_is_installed_once():
    configure_logging("INFO")
    configure_logging("INFO")
    access = logging.getLogger("uvicorn.access")
    assert sum(isinstance(f, SkipHealthChecks) for f in access.filters) == 1
