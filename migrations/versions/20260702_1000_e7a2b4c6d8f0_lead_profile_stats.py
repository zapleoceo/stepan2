"""lead profile stats: follower/following_count, last_active_at, profile_synced_at

Revision ID: e7a2b4c6d8f0
Revises: d5f1c9a2e7b3
Create Date: 2026-07-02 10:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e7a2b4c6d8f0"
down_revision = "d5f1c9a2e7b3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("lead")}
    if "follower_count" not in cols:
        op.add_column("lead", sa.Column("follower_count", sa.Integer(), nullable=True))
    if "following_count" not in cols:
        op.add_column("lead", sa.Column("following_count", sa.Integer(), nullable=True))
    if "last_active_at" not in cols:
        op.add_column("lead", sa.Column("last_active_at", sa.DateTime(), nullable=True))
    if "profile_synced_at" not in cols:
        op.add_column("lead", sa.Column("profile_synced_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("lead", "profile_synced_at")
    op.drop_column("lead", "last_active_at")
    op.drop_column("lead", "following_count")
    op.drop_column("lead", "follower_count")
