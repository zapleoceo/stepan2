"""lead.lead_type — segment classification (hot/warm/cold/no_budget/student/non_target)

Revision ID: e1f2a3b4c5d6
Revises: d0e1f2a3b4c5
Create Date: 2026-07-05 05:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e1f2a3b4c5d6"
down_revision = "d0e1f2a3b4c5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("lead")}
    if "lead_type" not in cols:
        op.add_column("lead", sa.Column("lead_type", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("lead", "lead_type")
