"""Deduplication keys (PROJECT_PLAN.md §3 Deduplication).

Key = first available of entry GUID, normalized link, hash of title + publish
date; stored as "<rule>:<sha256 hex>" so the rule that produced it is visible.
"""

import hashlib
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TRACKING_PARAMS = {
    "fbclid", "gclid", "dclid", "msclkid", "yclid", "igshid",
    "mc_cid", "mc_eid", "_hsenc", "_hsmi", "mkt_tok",
}  # fmt: skip


def normalize_link(url: str) -> str:
    """Lowercase scheme and host, drop the fragment, strip tracking query params."""
    parts = urlsplit(url.strip())
    netloc = parts.netloc.lower()
    query = urlencode(
        [
            (k, v)
            for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if not k.lower().startswith("utm_") and k.lower() not in _TRACKING_PARAMS
        ]
    )
    return urlunsplit((parts.scheme.lower(), netloc, parts.path, query, ""))


def _hashed(rule: str, value: str) -> str:
    return f"{rule}:{hashlib.sha256(value.encode()).hexdigest()}"


def dedup_key(
    *,
    guid: str | None,
    link: str | None,
    title: str,
    published: str | None,
    mode: Literal["link"] | None = None,
) -> str:
    """`link` is expected to be normalized already."""
    if guid and guid.strip() and mode != "link":
        return _hashed("guid", guid.strip())
    if link:
        return _hashed("link", link)
    return _hashed("hash", f"{title}\x00{published or ''}")
