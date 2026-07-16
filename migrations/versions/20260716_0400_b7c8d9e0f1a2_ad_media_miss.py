"""ad_media_miss — stop re-hunting media that no ad will ever claim

Six lead media resolve to no ad in the account (88 leads). The map sync retried them every
20 minutes, and each retry is a full creatives walk that ends in an account-wide throttle
(measured: 11 aborted walks in 3 hours). That is not just waste — the throttle it earns is
the same budget a genuinely NEW ad needs to be discovered with, so the dead media were
actively degrading the thing the sync exists for.

This table remembers the misses so they can be skipped, with an exponential retry so a medium
that only looked dead (throttle, a creative not yet propagated) still gets another chance —
just not every tick forever.

Revision ID: b7c8d9e0f1a2
Revises: a6b7c8d9e0f1
Create Date: 2026-07-16 04:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b7c8d9e0f1a2"
down_revision = "a6b7c8d9e0f1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "ad_media_miss" in set(sa.inspect(op.get_bind()).get_table_names()):
        return
    op.create_table(
        "ad_media_miss",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("branch_id", sa.Integer(), sa.ForeignKey("branch.id"), nullable=False),
        sa.Column("media_pk", sa.String(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_try_at", sa.DateTime(), nullable=False),
        # Inline, not op.create_unique_constraint(): SQLite has no ALTER ADD CONSTRAINT and
        # the standalone call breaks the on-SQLite alembic run in tests.
        sa.UniqueConstraint("branch_id", "media_pk", name="uq_admiss_branch_media"),
    )
    op.create_index("ix_ad_media_miss_media_pk", "ad_media_miss", ["media_pk"])


def downgrade() -> None:
    op.drop_table("ad_media_miss")
