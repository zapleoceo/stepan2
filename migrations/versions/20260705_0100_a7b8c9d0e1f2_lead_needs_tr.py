"""lead.needs_tr — per-lang cache of translated needs (jobs/pains/gains)

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-07-05 01:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a7b8c9d0e1f2"
down_revision = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("lead")}
    if "needs_tr" not in cols:
        op.add_column("lead", sa.Column("needs_tr", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("lead", "needs_tr")
