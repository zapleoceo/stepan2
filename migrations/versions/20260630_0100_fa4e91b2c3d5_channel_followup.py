"""add next_followup_at to channel_thread

Revision ID: fa4e91b2c3d5
Revises: e3f9c2d84b61
Create Date: 2026-06-30 01:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "fa4e91b2c3d5"
down_revision = "e3f9c2d84b61"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("channel_thread")}
    if "next_followup_at" not in cols:
        op.add_column(
            "channel_thread",
            sa.Column("next_followup_at", sa.DateTime(), nullable=True),
        )


def downgrade() -> None:
    op.drop_column("channel_thread", "next_followup_at")
