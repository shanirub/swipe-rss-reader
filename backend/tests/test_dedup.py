import pytest

from swipe_rss.dedup import dedup_key, normalize_link


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("HTTPS://Example.COM/Path?a=1#frag", "https://example.com/Path?a=1"),
        ("https://x.com/p?utm_source=rss&utm_medium=feed&id=7", "https://x.com/p?id=7"),
        ("https://x.com/p?fbclid=abc&gclid=def", "https://x.com/p"),
        ("  https://x.com/p  ", "https://x.com/p"),
    ],
)
def test_normalize_link(raw, expected):
    assert normalize_link(raw) == expected


def test_guid_wins():
    key = dedup_key(guid="abc", link="https://x.com/p", title="t", published=None)
    assert key.startswith("guid:")


def test_link_when_no_guid():
    key = dedup_key(guid=None, link="https://x.com/p", title="t", published=None)
    assert key.startswith("link:")


def test_hash_when_no_guid_or_link():
    a = dedup_key(guid=None, link=None, title="t", published="Mon, 1 Jan 2026")
    b = dedup_key(guid=None, link=None, title="t", published="Tue, 2 Jan 2026")
    assert a.startswith("hash:") and a != b


def test_link_mode_ignores_guid():
    key = dedup_key(guid="unstable-123", link="https://x.com/p", title="t", published=None, mode="link")
    assert key == dedup_key(guid=None, link="https://x.com/p", title="t", published=None)


def test_blank_guid_is_ignored():
    assert dedup_key(guid="  ", link="https://x.com/p", title="t", published=None).startswith("link:")
