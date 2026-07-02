"""block chat + clear-history: lead.is_blocked, channel_thread.context_cleared_at

Revision ID: a1c2e3f4b5d6
Revises: f8b3c5d7e9a1
Create Date: 2026-07-02 16:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a1c2e3f4b5d6"
down_revision = "f8b3c5d7e9a1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    lead_cols = {c["name"] for c in inspector.get_columns("lead")}
    if "is_blocked" not in lead_cols:
        op.add_column(
            "lead",
            sa.Column("is_blocked", sa.Boolean(), nullable=False, server_default=sa.false()),
        )
    thread_cols = {c["name"] for c in inspector.get_columns("channel_thread")}
    if "context_cleared_at" not in thread_cols:
        op.add_column(
            "channel_thread", sa.Column("context_cleared_at", sa.DateTime(), nullable=True)
        )


def downgrade() -> None:
    op.drop_column("channel_thread", "context_cleared_at")
    op.drop_column("lead", "is_blocked")
