"""swipes are append-only

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-06

The swipe log is the training data and can't be refetched (PROJECT_PLAN.md §3 Retention).
Triggers reject every DELETE and UPDATE on swipes, whatever the code path: a bug, a future
job, a manual sqlite3 session. Undo (stage 8) is a new event, not an update.

Caution: a batch-mode migration that rebuilds swipes (copy, drop, rename) drops these triggers
silently; tests/test_migrations.py checks them at head, so such a migration must recreate them.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for event in ("DELETE", "UPDATE"):
        op.execute(
            f"CREATE TRIGGER swipes_no_{event.lower()} BEFORE {event} ON swipes "
            "BEGIN SELECT RAISE(ABORT, 'swipes are append-only'); END"
        )


def downgrade() -> None:
    op.execute("DROP TRIGGER swipes_no_update")
    op.execute("DROP TRIGGER swipes_no_delete")
