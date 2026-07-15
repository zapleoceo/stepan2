"""message.media_attempts / media_next_try_at — back-off for media recognition retries

The backfill cron runs every 3 minutes and retried a failing recognition on EVERY tick for
the whole 6h window (~120 submits of the same image). The broker already retries 8 times
with its own backoff, so this stacked into a second retry layer. These two columns let the
backfill space its attempts out exponentially instead.

Additive + idempotent. Existing rows default to (0, NULL) = "try on the next tick", which
is exactly the old behaviour for the first attempt.

Revision ID: f4a5b6c7d8e0
Revises: e3f4a5b6c8d9
Create Date: 2026-07-15 10:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f4a5b6c7d8e0"
down_revision = "e3f4a5b6c8d9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cols = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("message")}
    if "media_attempts" not in cols:
        op.add_column("message", sa.Column(
            "media_attempts", sa.Integer(), nullable=False, server_default="0"))
    if "media_next_try_at" not in cols:
        op.add_column("message", sa.Column(
            "media_next_try_at", sa.DateTime(), nullable=True))
        op.create_index("ix_message_media_next_try_at", "message", ["media_next_try_at"])


def downgrade() -> None:
    op.drop_index("ix_message_media_next_try_at", table_name="message")
    op.drop_column("message", "media_next_try_at")
    op.drop_column("message", "media_attempts")
