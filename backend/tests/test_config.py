"""Runtime settings (swipe_rss/config.py): every setting comes from its environment variable."""

from pathlib import Path

from swipe_rss.config import get_settings


def test_settings_come_from_the_environment(monkeypatch):
    # In the containers these point into the volume and the config mount (compose.yaml); a typo
    # in a variable name would silently fall back to a path inside the container.
    monkeypatch.setenv("SWIPE_RSS_DB", "/data/swipe_rss.db")
    monkeypatch.setenv("SWIPE_RSS_FEEDS", "/config/feeds.toml")
    monkeypatch.setenv("SWIPE_RSS_API_TOKEN", "secret-token")
    monkeypatch.setenv("SWIPE_RSS_LOG_LEVEL", "debug")
    monkeypatch.setenv("SWIPE_RSS_BACKUP_DIR", "/backups")
    settings = get_settings()
    assert settings.db_path == Path("/data/swipe_rss.db")
    assert settings.feeds_path == Path("/config/feeds.toml")
    assert settings.api_token == "secret-token"
    assert settings.log_level == "DEBUG"
    assert settings.backup_dir == Path("/backups")


def test_empty_token_counts_as_unset(monkeypatch):
    monkeypatch.setenv("SWIPE_RSS_API_TOKEN", "")
    assert get_settings().api_token is None
