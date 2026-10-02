"""Runtime settings from environment variables."""

import os
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class Settings:
    db_path: Path
    feeds_path: Path


def get_settings() -> Settings:
    return Settings(
        db_path=Path(os.environ.get("SWIPE_RSS_DB", _REPO_ROOT / "backend" / "data" / "swipe_rss.db")),
        feeds_path=Path(os.environ.get("SWIPE_RSS_FEEDS", _REPO_ROOT / "config" / "feeds.toml")),
    )
