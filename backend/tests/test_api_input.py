"""Input that never reaches the generated models (Key Question 11, point i), body size, and the
order of the checks: token first, then size, then parsing (api.py middleware).

Bodies that are valid JSON but invalid swipes are covered in test_api_endpoints.py.
"""

import asyncio
import json
import logging

import pytest
from fastapi.testclient import TestClient

from swipe_rss import api
from swipe_rss.api import MAX_BODY_BYTES, MIN_TOKEN_LENGTH, create_app
from swipe_rss.api_models import Error, HTTPValidationError
from swipe_rss.config import Settings

TOKEN = "t" * MIN_TOKEN_LENGTH
AUTH = {"Authorization": f"Bearer {TOKEN}"}
JSON = {"Content-Type": "application/json"}


def make_app(db_path):
    return create_app(Settings(db_path=db_path, feeds_path=db_path, api_token=TOKEN))


@pytest.fixture
def client(engine, db_path):  # `engine` migrates the temp database first
    return TestClient(make_app(db_path), headers=AUTH)


def error_types(response) -> list[str]:
    return [e.type for e in HTTPValidationError.model_validate(response.json()).detail]


# --- input that never becomes a valid model ---


def test_malformed_json_is_422(client):
    response = client.post("/swipes", content=b'{"swipes": [', headers=JSON)
    assert response.status_code == 422
    assert error_types(response) == ["json_invalid"]


@pytest.mark.parametrize("headers", [{"Content-Type": "text/plain"}, {}])
def test_body_without_json_content_type_is_422(client, headers):
    # The body is not parsed as JSON at all, even if it is valid JSON.
    response = client.post("/swipes", content=b'{"swipes": []}', headers=headers)
    assert response.status_code == 422
    assert error_types(response) == ["model_attributes_type"]


def test_empty_body_is_422(client):
    response = client.post("/swipes", content=b"", headers=JSON)
    assert response.status_code == 422
    assert error_types(response) == ["missing"]


def test_invalid_utf8_body_is_422_not_500(client):
    # Found by Schemathesis: the error response crashed on input bytes that aren't UTF-8.
    for headers in (JSON, {"Content-Type": "text/plain"}):
        assert client.post("/swipes", content=b"\x80", headers=headers).status_code == 422


# --- strict JSON types (found by Schemathesis) ---


def valid_swipe(**overrides) -> dict:
    card = {
        "feed_id": "a", "item_key": "guid:" + "0" * 64, "headline": "h", "summary": "", "link": None,
        "published_at": None, "fetched_at": "2026-10-05T10:00:00Z", "author": None, "tags": [],
    }  # fmt: skip
    swipe = {
        "swipe_id": "8e4f8b0e-1c1d-4d6a-9a8e-2f0b0a7c1d11", "action": "never", "swiped_at": "2026-10-05T10:01:00Z",
        "tz_offset_minutes": 180, "time_to_swipe_ms": 1200, "app_version": 1, "card": card,
    }  # fmt: skip
    for key, value in overrides.items():
        (card if key in card else swipe)[key] = value
    return swipe


def test_valid_swipe_control(client):
    assert client.post("/swipes", json={"swipes": [valid_swipe()]}).status_code == 200


@pytest.mark.parametrize(
    ("field", "value"),
    [("fetched_at", 0), ("swiped_at", 1759658400), ("tz_offset_minutes", "180"), ("app_version", "1"),
     ("tz_offset_minutes", 33.5), ("action", 1)],
)  # fmt: skip
def test_values_of_the_wrong_json_type_are_422(client, field, value):
    # Lax validation would convert these (0 → 1970-01-01, "180" → 180); the spec's JSON types don't.
    response = client.post("/swipes", json={"swipes": [valid_swipe(**{field: value})]})
    assert response.status_code == 422
    assert field in {e.loc[-1] for e in HTTPValidationError.model_validate(response.json()).detail}


def test_integral_floats_count_as_integers(client):
    # JSON Schema: 180.0 is an integer. Strict mode alone would reject it.
    body = (
        '{"swipes": ['
        + json.dumps(valid_swipe()).replace('"tz_offset_minutes": 180', '"tz_offset_minutes": 180.0')
        + "]}"
    )
    assert '"tz_offset_minutes": 180.0' in body
    assert client.post("/swipes", content=body, headers=JSON).status_code == 200


@pytest.mark.parametrize(
    ("field", "value", "status"),
    [("app_version", 2**31 - 1, 200), ("app_version", 2**31, 422), ("app_version", 2**63, 422),
     ("time_to_swipe_ms", 2**63 - 1, 200), ("time_to_swipe_ms", 2**63, 422)],
)  # fmt: skip
def test_integers_beyond_the_spec_maximum_are_422_not_500(client, field, value, status):
    # 2**63 crashed the insert (SQLite INTEGER is 64-bit) before the spec had maxima.
    assert client.post("/swipes", json={"swipes": [valid_swipe(**{field: value})]}).status_code == status


# --- timestamp range (found by Schemathesis: year 0 after UTC conversion crashed with 500) ---


@pytest.mark.parametrize(
    ("field", "value"),
    [("swiped_at", "0001-01-01T00:00:00+00:01"), ("swiped_at", "9999-12-31T23:59:59-01:00"),
     ("swiped_at", "1969-12-31T23:59:59Z"), ("swiped_at", "3000-01-01T00:00:00Z"),
     ("fetched_at", "3000-01-01T00:00:00Z"), ("published_at", "1969-12-31T23:59:59Z")],
)  # fmt: skip
def test_timestamps_outside_the_range_are_422_not_500(client, field, value):
    response = client.post("/swipes", json={"swipes": [valid_swipe(**{field: value})]})
    assert response.status_code == 422
    [error] = HTTPValidationError.model_validate(response.json()).detail
    assert error.loc[-1] == field


@pytest.mark.parametrize("value", ["1970-01-01T00:00:00Z", "2999-12-31T23:59:59.999999Z", "1970-01-01T02:00:00+02:00"])
def test_timestamps_at_the_range_boundaries_are_accepted(client, value):
    swipe = valid_swipe(swiped_at=value, fetched_at=value, published_at=value)
    assert client.post("/swipes", json={"swipes": [swipe]}).status_code == 200


# --- body size ---


def test_body_larger_than_the_limit_is_413(client):
    response = client.post("/swipes", content=b" " * (MAX_BODY_BYTES + 1), headers=JSON)
    assert response.status_code == 413
    Error.model_validate(response.json())


def test_body_of_exactly_the_limit_passes_the_size_check(engine, db_path, monkeypatch):
    monkeypatch.setattr(api, "MAX_BODY_BYTES", 100)
    client = TestClient(make_app(db_path), headers=AUTH)
    assert client.post("/swipes", content=b" " * 100, headers=JSON).status_code == 422  # parsed, not 413
    assert client.post("/swipes", content=b" " * 101, headers=JSON).status_code == 413


def test_chunked_body_over_the_limit_is_413(engine, db_path, monkeypatch):
    # A chunked body has no Content-Length; the limit must hold while streaming.
    monkeypatch.setattr(api, "MAX_BODY_BYTES", 100)
    client = TestClient(make_app(db_path), headers=AUTH)

    def chunks():
        for _ in range(5):
            yield b" " * 30

    response = client.post("/swipes", content=chunks(), headers=JSON)
    assert "content-length" not in {k.lower() for k in response.request.headers}
    assert response.status_code == 413


# --- token before body ---


def run_asgi(app, *, body_chunks: list[bytes], headers: list[tuple[bytes, bytes]]):
    """Sends one POST /swipes through the ASGI app; returns (status, bytes the app read)."""
    pulled = 0
    pending = list(body_chunks)

    async def receive():
        nonlocal pulled
        if pending:
            chunk = pending.pop(0)
            pulled += len(chunk)
            return {"type": "http.request", "body": chunk, "more_body": bool(pending)}
        return {"type": "http.disconnect"}

    sent = []

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http", "method": "POST", "path": "/swipes", "raw_path": b"/swipes", "query_string": b"",
        "headers": [(b"content-type", b"application/json"), *headers], "http_version": "1.1",
        "scheme": "http", "server": ("test", 80), "client": ("test", 1), "root_path": "",
    }  # fmt: skip
    asyncio.run(app(scope, receive, send))
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    return status, pulled


def test_without_token_the_body_is_not_read(engine, db_path):
    status, pulled = run_asgi(make_app(db_path), body_chunks=[b'{"swipes": [' + b"1," * 10_000] * 10, headers=[])
    assert (status, pulled) == (401, 0)


def test_with_token_the_body_is_read(engine, db_path):
    # Control for the test above: the same request with the token reaches the parser.
    status, pulled = run_asgi(
        make_app(db_path), body_chunks=[b'{"swipes": ['], headers=[(b"authorization", f"Bearer {TOKEN}".encode())]
    )
    assert status == 422 and pulled > 0


def test_malformed_body_without_token_is_401_and_not_logged(engine, db_path, caplog):
    client = TestClient(make_app(db_path))
    with caplog.at_level(logging.WARNING, logger="swipe_rss.api"):
        response = client.post("/swipes", content=b'{"swipes": [', headers=JSON)
    assert response.status_code == 401
    assert not caplog.records


def test_oversized_body_without_token_is_401_not_413(engine, db_path):
    client = TestClient(make_app(db_path))
    assert client.post("/swipes", content=b" " * (MAX_BODY_BYTES + 1), headers=JSON).status_code == 401


# --- logging of rejected batches ---


def test_logged_body_of_a_rejected_batch_is_capped(client, caplog, monkeypatch):
    monkeypatch.setattr(api, "LOG_BODY_MAX_CHARS", 50)
    body = {"swipes": [{"swipe_id": "not-a-uuid", "padding": "x" * 500}]}
    with caplog.at_level(logging.WARNING, logger="swipe_rss.api"):
        assert client.post("/swipes", json=body).status_code == 422
    [message] = [r.getMessage() for r in caplog.records]
    assert "truncated" in message and "x" * 100 not in message
    assert "not-a-uuid" in message and '"uuid_parsing"' in message  # swipe_ids and errors in full
    assert len(message) < 2000  # errors don't repeat the input (Pydantic puts it into every error)
