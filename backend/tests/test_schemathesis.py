"""Property-based API tests with Schemathesis (Key Question 11, point e).

Schemathesis reads api/openapi.yaml and generates requests for every operation: valid ones
and deliberately invalid ones (boundary lengths, odd Unicode, nulls, huge numbers, missing
fields). Every response is checked against the spec: no 5xx, documented status codes and
headers, response bodies matching their schemas, invalid data rejected, auth not ignored.
The example-based tests pick inputs by hand; this finds the inputs nobody thought of.

All requests go in-process to the ASGI app (no network): the schema is built in a fixture
with the app attached, so Schemathesis' own extra requests (e.g. the no-token request of its
auth check) use the app too. They don't pass through our TestClient, so the contract-coverage
recorder doesn't see them.
"""

from datetime import datetime

import contract
import pytest
import schemathesis
from conftest import migrate
from hypothesis import HealthCheck, settings

from swipe_rss.api import MIN_TOKEN_LENGTH, create_app
from swipe_rss.config import Settings
from swipe_rss.db import make_engine
from swipe_rss.timestamps import in_range

TOKEN = "t" * MIN_TOKEN_LENGTH
FEEDS = '[defaults]\nmax_item_age_hours = 24\n\n[[feeds]]\nid = "a"\nurl = "https://example.com/feed"\n'


@pytest.fixture(scope="module")
def api_schema(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("schemathesis")
    (tmp / "feeds.toml").write_text(FEEDS)
    engine = make_engine(tmp / "test.db")
    migrate(engine)
    app = create_app(Settings(db_path=tmp / "test.db", feeds_path=tmp / "feeds.toml", api_token=TOKEN))
    loaded = schemathesis.openapi.from_path(contract.find_spec())
    loaded.app = app
    yield loaded
    engine.dispose()


schema = schemathesis.pytest.from_fixture("api_schema")


@schema.auth()
class BearerToken:
    # Registered as auth (not forced as a header) so Schemathesis can still leave the token out
    # on purpose, to check that the API rejects such requests.
    def get(self, case, context):
        return TOKEN

    def set(self, case, data, context):
        case.headers = {**(case.headers or {}), "Authorization": f"Bearer {data}"}


@schemathesis.hook
def map_body(context, body):
    """Move request timestamps that lie outside the spec's documented range into it.

    Why: the spec states the range (1970-01-01T00:00:00Z <= t < 3000-01-01T00:00:00Z) only in the
    fields' descriptions, because JSON Schema has no keyword for date ranges (and a `pattern` on a
    date-time field breaks the generated Pydantic models). Schemathesis can't read prose: it would
    send e.g. "0001-01-01T00:00:00+00:01" as a valid timestamp and report the server's correct 422
    as "rejected a schema-compliant request". Discarding such bodies (a filter) dropped ~9 of 10,
    so instead the year becomes 2000 + year % 400: the Gregorian calendar repeats every 400 years,
    so 29 February stays a valid date; month, day, time and offset are kept. Every check stays on;
    the range itself is tested with fixed examples in test_api_input.py.
    """
    swipes = body.get("swipes") if isinstance(body, dict) else None
    for swipe in swipes if isinstance(swipes, list) else []:
        if not isinstance(swipe, dict):
            continue
        _move_into_range(swipe, "swiped_at")
        if isinstance(swipe.get("card"), dict):
            _move_into_range(swipe["card"], "fetched_at")
            _move_into_range(swipe["card"], "published_at")
    return body


def _move_into_range(container: dict, key: str) -> None:
    value = container.get(key)
    if not isinstance(value, str):
        return  # null or a deliberately wrong type: the server must reject those itself
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return  # not a timestamp at all: also for the server to reject
    if parsed.tzinfo is not None and not in_range(parsed):
        container[key] = f"{2000 + parsed.year % 400:04d}{value[4:]}"


@schema.parametrize()
@settings(max_examples=50, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_api_conforms_to_the_spec(case):
    # base_url: the spec is loaded from a file, which has no server URL to use.
    case.call_and_validate(base_url="http://testserver")
