"""lead.notify_topic_id — per-lead Telegram forum topic for alerts

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-07-05 00:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f6a7b8c9d0e1"
down_revision = "e5f6a7b8c9d0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("lead")}
    if "notify_topic_id" not in cols:
        op.add_column("lead", sa.Column("notify_topic_id", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("lead", "notify_topic_id")
