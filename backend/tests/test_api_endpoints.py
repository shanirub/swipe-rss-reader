import hashlib
import json
import logging
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from swipe_rss.api import MIN_TOKEN_LENGTH, create_app
from swipe_rss.api_models import Card, QueueResponse, SwipeBatchResult
from swipe_rss.config import Settings
from swipe_rss.models import Item, Saved, Swipe

TOKEN = "t" * MIN_TOKEN_LENGTH
AUTH = {"Authorization": f"Bearer {TOKEN}"}
T0 = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)


@pytest.fixture
def client(engine, db_path):  # `engine` migrates the temp database first
    app = create_app(Settings(db_path=db_path, feeds_path=db_path, api_token=TOKEN))
    return TestClient(app, headers=AUTH)


def add_item(engine, feed_id: str, key: str, hours_old: float | None, **extra) -> None:
    with Session(engine) as s, s.begin():
        s.add(
            Item(
                feed_id=feed_id,
                dedup_key=f"guid:{hashlib.sha256(key.encode()).hexdigest()}",
                headline=extra.pop("headline", f"{feed_id} {key}"),
                summary="",
                link=None,
                published_at=None if hours_old is None else T0 - timedelta(hours=hours_old),
                fetched_at=extra.pop("fetched_at", T0),
                author=None,
                tags=extra.pop("tags", None),
                **extra,
            )
        )


def queue(client, **params) -> list[dict]:
    response = client.get("/queue", params=params)
    assert response.status_code == 200, response.text
    return QueueResponse.model_validate(response.json()).model_dump(mode="json")["items"]


def swipe(card: dict, action: str = "never", **overrides) -> dict:
    return {
        "swipe_id": str(uuid.uuid4()),
        "action": action,
        "swiped_at": "2026-10-03T14:00:00+03:00",
        "tz_offset_minutes": 180,
        "time_to_swipe_ms": 1500,
        "app_version": 1,
        "card": card,
    } | overrides


def count(engine, model) -> int:
    with Session(engine) as s:
        return s.scalar(select(func.count()).select_from(model))


# --- GET /queue ---


def test_empty_queue(client):
    assert queue(client) == []


def test_queue_is_round_robin_oldest_first(client, engine):
    add_item(engine, "a", "a1", hours_old=5)
    add_item(engine, "a", "a2", hours_old=3)
    add_item(engine, "a", "a3", hours_old=1)
    add_item(engine, "b", "b1", hours_old=2)
    add_item(engine, "c", "c1", hours_old=None, fetched_at=T0 - timedelta(hours=4))  # undated: fetch time
    headlines = [card["headline"] for card in queue(client)]
    # round 1: oldest first across feeds (a1 5h, c1 4h, b1 2h); round 2: a2; round 3: a3
    assert headlines == ["a a1", "c c1", "b b1", "a a2", "a a3"]


def test_queue_respects_limit(client, engine):
    for i in range(5):
        add_item(engine, "a", f"x{i}", hours_old=i)
    assert len(queue(client, limit=2)) == 2


@pytest.mark.parametrize("limit", ["0", "201", "-1", "abc"])
def test_queue_rejects_bad_limit(client, limit):
    assert client.get("/queue", params={"limit": limit}).status_code == 422


def test_queue_cards_are_valid_swipe_cards(client, engine):
    add_item(engine, "a", "k1", hours_old=1, tags=json.dumps(["x", "y"]))
    [card] = queue(client)
    Card.model_validate(card)  # what the queue serves must be postable back unchanged
    assert card["tags"] == ["x", "y"] and card["item_key"].startswith("guid:")


def test_invalid_row_is_skipped_not_fatal(client, engine, caplog):
    add_item(engine, "a", "good", hours_old=2)
    add_item(engine, "a", "bad", hours_old=1, headline="")  # violates the Card schema (minLength 1)
    with caplog.at_level(logging.ERROR, logger="swipe_rss.queue"):
        headlines = [card["headline"] for card in queue(client)]
    assert headlines == ["a good"]
    assert any("not a valid API card" in r.getMessage() for r in caplog.records)


def test_feed_ids_fit_the_api():
    # feeds.toml ids end up in every card; they must satisfy the spec's FeedId.
    from annotated_types import MaxLen

    from swipe_rss.feeds import Feed

    def max_len(model, field):
        [limit] = [m.max_length for m in model.model_fields[field].metadata if isinstance(m, MaxLen)]
        return limit

    assert max_len(Feed, "id") <= max_len(Card, "feed_id")


# --- POST /swipes ---


def test_swipe_round_trip(client, engine):
    add_item(engine, "a", "k1", hours_old=1)
    [card] = queue(client)
    body = swipe(card, "never")

    response = client.post("/swipes", json={"swipes": [body]})

    assert response.status_code == 200
    assert SwipeBatchResult.model_validate(response.json()) == SwipeBatchResult(stored=1, duplicates=0)
    assert queue(client) == []  # swiped items leave the queue
    with Session(engine) as s:
        row = s.get(Swipe, body["swipe_id"])
        item = s.scalars(select(Item)).one()
    assert (row.action, row.feed_id, row.item_key, row.headline) == ("never", "a", card["item_key"], "a k1")
    assert row.swiped_at == datetime(2026, 10, 3, 11, 0, tzinfo=UTC)  # stored in UTC
    assert row.tz_offset_minutes == 180 and row.time_to_swipe_ms == 1500 and row.app_version == 1
    assert row.received_at is not None and json.loads(row.tags) == []
    assert item.swiped_at is not None  # flagged, not deleted
    assert count(engine, Saved) == 0  # only `save` creates a saved entry


def test_resent_batch_is_idempotent(client, engine):
    add_item(engine, "a", "k1", hours_old=1)
    [card] = queue(client)
    batch = {"swipes": [swipe(card, "save")]}

    first = client.post("/swipes", json=batch).json()
    second = client.post("/swipes", json=batch).json()

    assert first == {"stored": 1, "duplicates": 0}
    assert second == {"stored": 0, "duplicates": 1}
    assert count(engine, Swipe) == 1 and count(engine, Saved) == 1


def test_save_creates_one_saved_entry_per_article(client, engine):
    add_item(engine, "a", "k1", hours_old=1)
    [card] = queue(client)
    first, again = swipe(card, "save"), swipe(card, "save")  # two save events for the same article

    client.post("/swipes", json={"swipes": [first]})
    result = client.post("/swipes", json={"swipes": [again]}).json()

    assert result == {"stored": 1, "duplicates": 0}  # the second event is logged ...
    with Session(engine) as s:
        saved = s.scalars(select(Saved)).one()  # ... but there is still one saved entry
    assert saved.swipe_id == first["swipe_id"] and saved.extraction_status == "pending" and saved.attempts == 0


@pytest.mark.parametrize("action", ["never", "read_now"])
def test_only_save_creates_a_saved_entry(client, engine, action):
    add_item(engine, "a", "k1", hours_old=1)
    [card] = queue(client)
    client.post("/swipes", json={"swipes": [swipe(card, action)]})
    assert count(engine, Saved) == 0


def test_swipe_for_a_pruned_item_is_stored(client, engine):
    add_item(engine, "a", "k1", hours_old=1)
    [card] = queue(client)
    with Session(engine) as s, s.begin():
        s.query(Item).delete()  # pruned before the phone synced
    assert client.post("/swipes", json={"swipes": [swipe(card, "save")]}).json() == {"stored": 1, "duplicates": 0}
    assert count(engine, Swipe) == 1 and count(engine, Saved) == 1


@pytest.mark.parametrize(
    "bad",
    [
        {"action": "maybe"},
        {"swiped_at": "2026-10-03T14:00:00"},  # no offset
        {"tz_offset_minutes": 841},
        {"time_to_swipe_ms": -1},
        {"app_version": 0},
        {"swipe_id": "not-a-uuid"},
        {"unexpected": 1},  # unknown field
    ],
)
def test_one_invalid_swipe_rejects_the_whole_batch(client, engine, bad):
    add_item(engine, "a", "k1", hours_old=1)
    [card] = queue(client)
    batch = {"swipes": [swipe(card), swipe(card) | bad]}

    assert client.post("/swipes", json=batch).status_code == 422
    assert count(engine, Swipe) == 0  # nothing stored: all or nothing
    assert len(queue(client)) == 1


@pytest.mark.parametrize("body", [{"swipes": []}, {}, {"swipes": "x"}])
def test_malformed_batches_are_rejected(client, body):
    assert client.post("/swipes", json=body).status_code == 422


def test_oversized_batch_is_rejected(client, engine):
    add_item(engine, "a", "k1", hours_old=1)
    [card] = queue(client)
    assert client.post("/swipes", json={"swipes": [swipe(card) for _ in range(501)]}).status_code == 422


def test_card_must_be_a_valid_card(client, engine):
    add_item(engine, "a", "k1", hours_old=1)
    [card] = queue(client)
    tampered = card | {"headline": "x" * 1001}
    assert client.post("/swipes", json={"swipes": [swipe(tampered)]}).status_code == 422


def test_rejected_batches_are_logged(client, engine, caplog):
    add_item(engine, "a", "k1", hours_old=1)
    [card] = queue(client)
    bad = swipe(card, "maybe")
    with caplog.at_level(logging.WARNING, logger="swipe_rss.api"):
        assert client.post("/swipes", json={"swipes": [bad]}).status_code == 422
    [record] = [r for r in caplog.records if r.name == "swipe_rss.api"]
    assert "rejected swipe batch" in record.getMessage() and bad["swipe_id"] in record.getMessage()


def test_other_validation_errors_are_not_logged_as_swipes(client, caplog):
    with caplog.at_level(logging.WARNING, logger="swipe_rss.api"):
        assert client.get("/queue", params={"limit": "0"}).status_code == 422
    assert not [r for r in caplog.records if r.name == "swipe_rss.api"]


# --- gaps found by mutmut ---


def test_card_and_swipe_carry_every_field(client, engine):
    add_item(engine, "a", "full", hours_old=1, tags=json.dumps(["ünïcode", "b"]))
    with Session(engine) as s, s.begin():
        item = s.scalars(select(Item)).one()
        item.link, item.author, item.summary = "https://a.example/p", "Ann", "sum"
    [card] = queue(client)
    assert (card["link"], card["author"], card["summary"]) == ("https://a.example/p", "Ann", "sum")
    assert card["published_at"] == "2026-10-03T11:00:00Z" and card["tags"] == ["ünïcode", "b"]

    body = swipe(card, "save")
    client.post("/swipes", json={"swipes": [body]})

    with Session(engine) as s:
        row = s.get(Swipe, body["swipe_id"])
    assert (row.link, row.author, row.summary) == ("https://a.example/p", "Ann", "sum")
    assert row.published_at == T0 - timedelta(hours=1) and row.fetched_at == T0
    assert json.loads(row.tags) == ["ünïcode", "b"]


def test_batch_counts_every_new_swipe(client, engine):
    add_item(engine, "a", "k1", hours_old=2)
    add_item(engine, "a", "k2", hours_old=1)
    cards = queue(client)
    result = client.post("/swipes", json={"swipes": [swipe(c) for c in cards]}).json()
    assert result == {"stored": 2, "duplicates": 0}


def test_duplicate_does_not_stop_the_rest_of_the_batch(client, engine):
    add_item(engine, "a", "k1", hours_old=2)
    add_item(engine, "a", "k2", hours_old=1)
    first, second = queue(client)
    old = swipe(first)
    client.post("/swipes", json={"swipes": [old]})

    result = client.post("/swipes", json={"swipes": [old, swipe(second, "save")]}).json()

    assert result == {"stored": 1, "duplicates": 1}
    assert count(engine, Swipe) == 2 and count(engine, Saved) == 1 and queue(client) == []


def test_swipe_flags_only_its_own_item(client, engine):
    add_item(engine, "a", "k1", hours_old=2)
    add_item(engine, "a", "k2", hours_old=1)  # same feed, other article
    add_item(engine, "b", "k1", hours_old=1)  # same article (same key) in another feed
    target = next(c for c in queue(client) if c["feed_id"] == "a" and c["headline"] == "a k1")

    client.post("/swipes", json={"swipes": [swipe(target)]})

    assert sorted(c["headline"] for c in queue(client)) == ["a k2", "b k1"]


def test_second_swipe_keeps_the_first_swiped_at(client, engine):
    add_item(engine, "a", "k1", hours_old=1)
    [card] = queue(client)
    client.post("/swipes", json={"swipes": [swipe(card)]})
    with Session(engine) as s, s.begin():  # backdate, so an overwrite within the same second is visible
        s.scalars(select(Item)).one().swiped_at = T0
    client.post("/swipes", json={"swipes": [swipe(card, "save")]})  # e.g. a later change of mind
    with Session(engine) as s:
        assert s.scalars(select(Item.swiped_at)).one() == T0
