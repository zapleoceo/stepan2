"""lead.needs — captured customer-profile (jobs/pains/gains) JSON

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-07-04 09:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c3d4e5f6a7b8"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("lead")}
    if "needs" not in cols:
        op.add_column("lead", sa.Column("needs", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("lead", "needs")
