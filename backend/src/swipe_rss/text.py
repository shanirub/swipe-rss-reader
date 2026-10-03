"""HTML → plain text for feed summaries (items are stored as plain text)."""

import re
from html.parser import HTMLParser

_BLOCK_TAGS = {"p", "br", "div", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "tr"}
_SKIP_TAGS = {"script", "style"}
_WS = re.compile(r"\s+")


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        elif tag in _BLOCK_TAGS:
            self.parts.append(" ")

    def handle_data(self, data):
        if not self._skip_depth:
            self.parts.append(data)


def truncate(text: str, max_chars: int) -> str:
    """Cut to at most `max_chars` characters, marking the cut with "…"."""
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def html_to_text(html: str, max_chars: int | None = None) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    text = _WS.sub(" ", "".join(parser.parts)).strip()
    return truncate(text, max_chars) if max_chars is not None else text
