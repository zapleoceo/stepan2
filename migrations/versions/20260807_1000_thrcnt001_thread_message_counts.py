"""channel_thread.msg_in / msg_out: counted once by the writer, not by every reader

The inbox recomputed both with a per-thread LATERAL on every poll, every 30 seconds, for a
number that changes only when a message is inserted — 414 million index lookups against
`message` in the life of this database.

The consolidated lead list makes that worse rather than better: the same counts, grouped per
LEAD, multiply by however many connectors one person is on. So the count moves to the write
side, where it costs one increment.

Backfilled from the rows that exist, which is the only truth available.

Revision ID: thrcnt001
Revises: trlang0001
Create Date: 2026-08-07 10:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "thrcnt001"
down_revision = "trlang0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("channel_thread",
                  sa.Column("msg_in", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("channel_thread",
                  sa.Column("msg_out", sa.Integer(), nullable=False, server_default="0"))
    # Correlated subqueries rather than UPDATE…FROM: the latter is Postgres-only and the
    # test schema is SQLite, so the migration would pass in production and fail in CI — the
    # worst way round. One-off over a few thousand rows either way.
    op.execute(
        "UPDATE channel_thread SET"
        " msg_in = (SELECT COUNT(*) FROM message m"
        "           WHERE m.thread_id = channel_thread.id AND m.direction = 'in'),"
        " msg_out = (SELECT COUNT(*) FROM message m"
        "            WHERE m.thread_id = channel_thread.id AND m.direction = 'out')"
    )


def downgrade() -> None:
    op.drop_column("channel_thread", "msg_out")
    op.drop_column("channel_thread", "msg_in")
