import pytest

from swipe_rss.dedup import dedup_key, normalize_link


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("HTTPS://Example.COM/Path?a=1#frag", "https://example.com/Path?a=1"),
        ("https://x.com/p?utm_source=rss&utm_medium=feed&id=7", "https://x.com/p?id=7"),
        ("https://x.com/p?fbclid=abc&gclid=def", "https://x.com/p"),
        ("  https://x.com/p  ", "https://x.com/p"),
        ("https://x.com/p?a=&b=1", "https://x.com/p?a=&b=1"),  # empty parameters are kept
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


def test_keys_are_stable():
    # Golden values: dedup keys are stored permanently (tombstones, swipes.item_key) as item identity.
    # If this test fails, the key function changed: existing articles would get new keys, come back as
    # duplicates and lose their link to past swipes. Change the expected values only with a migration plan.
    assert dedup_key(guid="g1", link=None, title="t", published=None) == (
        "guid:711430f6164e93803d93428bc1fab80f41e213bb197689307de8606d437c3038"
    )
    assert dedup_key(guid=None, link="https://example.com/p", title="t", published=None) == (
        "link:9678caa8b05c2fadb331b103bcd348c79b5e85bd2bef1aa827c72670174b8890"
    )
    assert dedup_key(guid=None, link=None, title="Title", published="Mon, 01 Jan 2026 00:00:00 +0000") == (
        "hash:28f6c673e60ada151dab3a7875e51ed72976e14e0528b17a298293aab21762ff"
    )
    assert dedup_key(guid=None, link=None, title="Title", published=None) == (
        "hash:b51c7cfc77047ad40f58d9dd250bc742fd0c58ce2aea85f475e6a3d6a3cac5fd"
    )
