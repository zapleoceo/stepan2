"""alert reping_at — SLA re-ping timestamp on manager_alert

A ready/handoff alert the manager hasn't worked within the SLA gets one polite re-ping
tagging the branch manager. reping_at marks that the re-ping went out, so it fires once.

Revision ID: d9e0f1a2b3c4
Revises: c8d9e0f1a2b3
Create Date: 2026-07-20 06:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d9e0f1a2b3c4"
down_revision = "c8d9e0f1a2b3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cols = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("manager_alert")}
    if "reping_at" not in cols:
        op.add_column("manager_alert", sa.Column("reping_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("manager_alert", "reping_at")
