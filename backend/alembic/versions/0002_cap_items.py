"""cap existing items to the API card limits

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-03

Rows stored before the fetcher enforced card limits must also be valid API cards,
otherwise a swipe on them is rejected forever (PROJECT_PLAN.md §3 Swipe recording).
The limits are copied here on purpose, not imported from the fetcher: a migration must
keep doing what it did when it was written.
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

HEADLINE_MAX_CHARS = 1000
SUMMARY_MAX_CHARS = 2000
AUTHOR_MAX_CHARS = 500
LINK_MAX_CHARS = 4096
TAGS_MAX = 50
TAG_MAX_CHARS = 200


def _truncate(text: str, max_chars: int) -> str:
    return text if len(text) <= max_chars else text[: max_chars - 1].rstrip() + "…"


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, headline, summary, link, author, tags FROM items")).mappings().all()
    for row in rows:
        tags = json.loads(row["tags"]) if row["tags"] else []
        capped_tags = [_truncate(t, TAG_MAX_CHARS) for t in tags[:TAGS_MAX]]
        new = {
            "id": row["id"],
            "headline": _truncate(row["headline"], HEADLINE_MAX_CHARS),
            "summary": _truncate(row["summary"], SUMMARY_MAX_CHARS),
            "link": row["link"] if row["link"] is None or len(row["link"]) <= LINK_MAX_CHARS else None,
            "author": _truncate(row["author"], AUTHOR_MAX_CHARS) if row["author"] else row["author"],
            "tags": json.dumps(capped_tags, ensure_ascii=False) if capped_tags != tags else row["tags"],
        }
        if any(new[k] != row[k] for k in new):
            conn.execute(
                sa.text(
                    "UPDATE items SET headline = :headline, summary = :summary, link = :link,"
                    " author = :author, tags = :tags WHERE id = :id"
                ),
                new,
            )


def downgrade() -> None:
    # Truncation can't be undone, and the schema is unchanged: nothing to do.
    pass
