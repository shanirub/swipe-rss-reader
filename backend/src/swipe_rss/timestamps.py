"""The range of timestamps the API accepts (PROJECT_PLAN.md §3 API conventions).

JSON Schema has no keyword for a date range, so the spec states it in prose and the code
enforces it here. Outside it, converting a valid RFC 3339 timestamp to UTC can leave Python's
datetime range (year 1..9999): "0001-01-01T00:00:00+00:01" is year 0 in UTC, and storing it
crashed with a 500 (found by Schemathesis).
"""

from datetime import UTC, datetime

TIMESTAMP_MIN = datetime(1970, 1, 1, tzinfo=UTC)  # inclusive
TIMESTAMP_END = datetime(3000, 1, 1, tzinfo=UTC)  # exclusive


def in_range(value: datetime) -> bool:
    try:
        return TIMESTAMP_MIN <= value < TIMESTAMP_END
    except OverflowError:  # comparing across offsets at the edge of datetime's range
        return False
