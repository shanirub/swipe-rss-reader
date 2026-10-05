"""Load and validate feeds.toml.

Any problem raises FeedsFileError so the fetch run aborts: a typo must never be
interpreted as "all feeds removed".
"""

import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, PositiveInt, ValidationError, model_validator


class FeedsFileError(Exception):
    pass


class Defaults(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_item_age_hours: PositiveInt | None = None


class Feed(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$", max_length=100)  # = FeedId in api/openapi.yaml
    url: str = Field(pattern=r"^https?://\S+$")
    name: str | None = None
    dedup: Literal["link"] | None = None


class FeedsFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    defaults: Defaults = Defaults()
    feeds: list[Feed] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_ids(self) -> FeedsFile:
        seen: set[str] = set()
        for feed in self.feeds:
            if feed.id in seen:
                raise ValueError(f"duplicate feed id: {feed.id}")
            seen.add(feed.id)
        return self


def load_feeds(path: Path) -> FeedsFile:
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
        return FeedsFile.model_validate(data)
    # UnicodeDecodeError: a file saved in another encoding (found by Schemathesis via GET /feeds).
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError, ValidationError) as e:
        raise FeedsFileError(f"{path}: {e}") from e
