"""lead.guard_regen_count — per-lead 'the cheap model stumbles here' signal for routing

Revision ID: f8a9b0c1d2e3
Revises: e7f8a9b0c1d2
Create Date: 2026-07-10 21:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f8a9b0c1d2e3"
down_revision = "e7f8a9b0c1d2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("lead")}
    if "guard_regen_count" not in cols:
        op.add_column(
            "lead",
            sa.Column("guard_regen_count", sa.Integer(), nullable=False, server_default="0"),
        )


def downgrade() -> None:
    op.drop_column("lead", "guard_regen_count")
