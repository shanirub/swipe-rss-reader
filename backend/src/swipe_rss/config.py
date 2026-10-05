"""Runtime settings from environment variables."""

import os
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class Settings:
    db_path: Path
    feeds_path: Path
    api_token: str | None  # bearer token for the API; the API refuses to start without it
    log_level: str = "INFO"  # DEBUG, INFO, WARNING, ERROR; applies to the jobs and the API


def get_settings() -> Settings:
    return Settings(
        db_path=Path(os.environ.get("SWIPE_RSS_DB", _REPO_ROOT / "backend" / "data" / "swipe_rss.db")),
        feeds_path=Path(os.environ.get("SWIPE_RSS_FEEDS", _REPO_ROOT / "config" / "feeds.toml")),
        api_token=os.environ.get("SWIPE_RSS_API_TOKEN") or None,
        log_level=os.environ.get("SWIPE_RSS_LOG_LEVEL", "INFO").upper(),
    )
