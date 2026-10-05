"""The API's timestamp range (swipe_rss/timestamps.py)."""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from swipe_rss.timestamps import TIMESTAMP_END, TIMESTAMP_MIN, in_range


@pytest.mark.parametrize(
    ("value", "expected"),
    [(TIMESTAMP_MIN, True), (TIMESTAMP_MIN - timedelta(microseconds=1), False),
     (TIMESTAMP_END - timedelta(microseconds=1), True), (TIMESTAMP_END, False),
     (datetime(2026, 10, 5, 12, tzinfo=timezone(timedelta(hours=3))), True),
     (datetime(1, 1, 1, tzinfo=timezone(timedelta(minutes=1))), False),  # year 0 in UTC
     (datetime(9999, 12, 31, 23, 59, tzinfo=timezone(timedelta(hours=-1))), False)],  # year 10000 in UTC
)  # fmt: skip
def test_range(value, expected):
    assert in_range(value) is expected


def test_limits_are_utc():
    assert TIMESTAMP_MIN.tzinfo is UTC and TIMESTAMP_END.tzinfo is UTC
