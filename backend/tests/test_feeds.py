import pytest

from swipe_rss.config import get_settings
from swipe_rss.feeds import FeedsFileError, load_feeds


def write(tmp_path, text: str):
    path = tmp_path / "feeds.toml"
    path.write_text(text)
    return path


def test_repo_feeds_file_is_valid():
    feeds = load_feeds(get_settings().feeds_path)
    assert feeds.defaults.max_item_age_hours == 24
    assert len(feeds.feeds) >= 1


def test_minimal_file(tmp_path):
    feeds = load_feeds(write(tmp_path, '[[feeds]]\nid = "a"\nurl = "https://example.com/feed"\n'))
    assert feeds.feeds[0].id == "a"
    assert feeds.defaults.max_item_age_hours is None


@pytest.mark.parametrize(
    "text",
    [
        "",  # no feeds at all: must not mean "all feeds removed"
        "[[feeds]]\nid = 'a'\nurl = 'https://x'\n[[feeds]]\nid = 'a'\nurl = 'https://y'\n",  # duplicate id
        "[[feeds]]\nid = 'Bad_ID'\nurl = 'https://x'\n",  # not a slug
        f"[[feeds]]\nid = '{'a' * 101}'\nurl = 'https://x'\n",  # longer than the API allows
        "[[feeds]]\nid = 'a'\nurl = 'ftp://x'\n",  # not http(s)
        "[[feeds]]\nid = 'a'\nurl = 'https://x'\ndedupe = 'link'\n",  # typo'd key
        "[defaults]\nmax_item_age_hour = 24\n[[feeds]]\nid = 'a'\nurl = 'https://x'\n",  # typo'd default
        "[[feeds]\nid = 'a'\n",  # TOML syntax error
    ],
)
def test_invalid_files_raise(tmp_path, text):
    with pytest.raises(FeedsFileError):
        load_feeds(write(tmp_path, text))


def test_missing_file_raises(tmp_path):
    with pytest.raises(FeedsFileError):
        load_feeds(tmp_path / "nope.toml")
