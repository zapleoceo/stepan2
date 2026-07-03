"""read-receipt: channel_thread.lead_seen_at

Revision ID: c4e9b1d7a2f5
Revises: b2d4f6a8c1e3
Create Date: 2026-07-03 04:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c4e9b1d7a2f5"
down_revision = "b2d4f6a8c1e3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("channel_thread")}
    if "lead_seen_at" not in cols:
        op.add_column(
            "channel_thread", sa.Column("lead_seen_at", sa.DateTime(), nullable=True)
        )


def downgrade() -> None:
    op.drop_column("channel_thread", "lead_seen_at")
