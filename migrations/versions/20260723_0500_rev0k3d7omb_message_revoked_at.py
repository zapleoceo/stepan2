"""message.revoked_at — unsent messages are tombstoned, not deleted

Recalling a bot message used to hard-delete our copy once IG confirmed the unsend. That row
was the only evidence the message had been OURS: the inbox poll runs every two minutes and had
already seen it, so the content dedup in ingest._store_outgoing found no match, filed it as a
manager's manual reply, and handed the whole thread to a human with the bot muted — every
recalled message reappearing as a "manager" bubble (thread 4954).

A revoked row is kept instead. It is hidden from the chat, excluded from the model's dialog,
and skipped by the watermark rewind, but it still answers the one question the dedup asks.

Additive and idempotent. Rows recalled before this migration are already gone and cannot be
recovered; the threads they affected need their stage set back by hand.

Revision ID: rev0k3d7omb
Revises: ad1f1rst0nly
Create Date: 2026-07-23 05:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "rev0k3d7omb"
down_revision = "ad1f1rst0nly"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cols = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("message")}
    if "revoked_at" not in cols:
        op.add_column("message", sa.Column("revoked_at", sa.DateTime(), nullable=True))
        op.create_index("ix_message_revoked_at", "message", ["revoked_at"])


def downgrade() -> None:
    op.drop_index("ix_message_revoked_at", table_name="message")
    op.drop_column("message", "revoked_at")
