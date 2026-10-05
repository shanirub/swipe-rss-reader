# GENERATED from api/openapi.yaml by datamodel-codegen. Do not edit.
# Regenerate: cd backend && uv run datamodel-codegen

from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, RootModel


class Action(StrEnum):
    never = "never"
    save = "save"
    read_now = "read_now"


class ExtractionStatus(StrEnum):
    pending = "pending"
    done = "done"
    failed = "failed"


class Tag(RootModel[str]):
    root: Annotated[str, Field(max_length=200)]


class Card(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    feed_id: Annotated[
        str, Field(description="Stable feed slug from feeds.toml.", max_length=100, pattern="^[a-z0-9]+(-[a-z0-9]+)*$")
    ]
    item_key: Annotated[
        str,
        Field(
            description="The item's dedup key; `<rule>:<sha256 hex>`. Opaque to the client.",
            pattern="^(guid|link|hash):[0-9a-f]{64}$",
        ),
    ]
    headline: Annotated[str, Field(max_length=1000, min_length=1)]
    summary: Annotated[str, Field(description="Plain text; may be empty.", max_length=2000)]
    link: Annotated[
        str | None,
        Field(description="Normalized article URL; null if missing or longer than the limit.", max_length=4096),
    ]
    published_at: Annotated[
        AwareDatetime | None,
        Field(description="1970-01-01T00:00:00Z ≤ t < 3000-01-01T00:00:00Z (server-enforced, 422)."),
    ]
    fetched_at: Annotated[
        AwareDatetime, Field(description="1970-01-01T00:00:00Z ≤ t < 3000-01-01T00:00:00Z (server-enforced, 422).")
    ]
    author: Annotated[str | None, Field(max_length=500)]
    tags: Annotated[list[Tag], Field(max_length=50)]


class QueueResponse(BaseModel):
    items: list[Card]


class Swipe(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    swipe_id: Annotated[UUID, Field(description="Generated on the phone at swipe time; idempotency key.")]
    action: Action
    swiped_at: Annotated[
        AwareDatetime,
        Field(description="Phone clock with offset; 1970-01-01T00:00:00Z ≤ t < 3000-01-01T00:00:00Z (422 otherwise)."),
    ]
    tz_offset_minutes: Annotated[int, Field(description="Phone's UTC offset at swipe time.", ge=-840, le=840)]
    time_to_swipe_ms: Annotated[
        int | None, Field(description="How long the card was on screen; null if unknown.", ge=0, le=9223372036854775807)
    ]
    app_version: Annotated[int, Field(description="App versionCode.", ge=1, le=2147483647)]
    card: Card


class SwipeBatch(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    swipes: Annotated[list[Swipe], Field(max_length=500, min_length=1)]


class SwipeBatchResult(BaseModel):
    stored: Annotated[int, Field(description="Swipes newly stored by this request.", ge=0)]
    duplicates: Annotated[int, Field(description="Swipes already stored earlier (by `swipe_id`).", ge=0)]


class SavedEntry(BaseModel):
    feed_id: Annotated[
        str, Field(description="Stable feed slug from feeds.toml.", max_length=100, pattern="^[a-z0-9]+(-[a-z0-9]+)*$")
    ]
    item_key: Annotated[
        str,
        Field(
            description="The item's dedup key; `<rule>:<sha256 hex>`. Opaque to the client.",
            pattern="^(guid|link|hash):[0-9a-f]{64}$",
        ),
    ]
    headline: str
    summary: str
    link: str | None
    published_at: AwareDatetime | None
    saved_at: Annotated[AwareDatetime, Field(description="`swiped_at` of the save swipe.")]
    extraction_status: ExtractionStatus
    read_at: Annotated[AwareDatetime | None, Field(description="Always null until stage 6.")]


class SavedListResponse(BaseModel):
    items: list[SavedEntry]


class SavedContent(BaseModel):
    extraction_status: ExtractionStatus
    text: Annotated[
        str | None,
        Field(description="Extracted plain text; non-null only when `done`. May be teaser-only for paywalled pages."),
    ]
    extracted_at: AwareDatetime | None
    last_error: str | None


class FeedStatus(BaseModel):
    feed_id: Annotated[
        str, Field(description="Stable feed slug from feeds.toml.", max_length=100, pattern="^[a-z0-9]+(-[a-z0-9]+)*$")
    ]
    name: str | None
    url: str
    last_attempt_at: AwareDatetime | None
    last_success_at: AwareDatetime | None
    last_new_item_at: AwareDatetime | None
    last_error: str | None
    consecutive_failures: Annotated[int, Field(ge=0)]


class FeedListResponse(BaseModel):
    feeds: list[FeedStatus]


class Health(BaseModel):
    status: Literal["ok"]


class Error(BaseModel):
    detail: str


class DetailItem(BaseModel):
    loc: list[str | int]
    msg: str
    type: str


class HTTPValidationError(BaseModel):
    detail: list[DetailItem]
