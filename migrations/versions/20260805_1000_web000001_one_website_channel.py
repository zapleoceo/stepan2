"""one website channel per install, enforced by the schema

`website_branch_id` answers "which branch does the public landing page sell from?" by finding
the active branch that owns an active `website` channel. A second such channel is therefore a
second answer: the resolver picks the lowest branch id and logs a warning, but the loser is
still a live branch with a full prompt-library clone, listed in the operator UI and walked by
every per-branch cron.

The application guards this with an asyncio.Lock in `ensure_website_branch`, which covers one
uvicorn worker and nothing more. This partial unique index is the guarantee that does not
depend on the process topology — the second writer gets an IntegrityError and adopts the
branch the winner created.

Partial on purpose: Instagram, WhatsApp and Meta branches keep as many channels as they want.

Safe on live data: `website` is a kind introduced by this same release, so no row can carry it
yet. The index is created anyway rather than assumed — a failed CREATE UNIQUE INDEX aborts the
migrate-first deploy loudly, which is the correct outcome if that assumption is ever wrong.

Revision ID: web000001
Revises: plib000001
Create Date: 2026-08-05 10:00:00
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "web000001"
down_revision = "plib000001"
branch_labels = None
depends_on = None

_INDEX = "uq_channel_one_website"


def upgrade() -> None:
    # Postgres and SQLite spell a partial index the same way; alembic's create_index carries
    # the predicate through the dialect kwargs, so one call serves both.
    op.create_index(
        _INDEX, "channel", ["kind"], unique=True,
        sqlite_where=text("kind = 'website'"),
        postgresql_where=text("kind = 'website'"),
    )


def downgrade() -> None:
    op.drop_index(_INDEX, table_name="channel")
