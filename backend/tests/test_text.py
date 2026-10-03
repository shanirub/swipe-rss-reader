from swipe_rss.text import html_to_text, truncate


def test_strips_tags_and_unescapes():
    assert html_to_text("<p>Hello&nbsp;<b>world</b> &amp; co</p><p>next</p>") == "Hello world & co next"


def test_drops_script_and_style():
    assert html_to_text("<style>p{}</style>a<script>alert(1)</script>b") == "ab"


def test_truncates():
    out = html_to_text("word " * 100, max_chars=20)
    assert len(out) <= 20 and out.endswith("…")


def test_plain_text_passthrough():
    assert html_to_text("already plain") == "already plain"


def test_truncate_boundary():
    assert truncate("abcde", 5) == "abcde"  # at the limit: unchanged
    assert truncate("abcdef", 5) == "abcd…"  # over: exactly max_chars, marked
    assert len(truncate("x" * 10_000, 1000)) == 1000


def test_truncate_drops_whitespace_before_the_ellipsis():
    assert truncate("word word", 6) == "word…"  # not "word …"
