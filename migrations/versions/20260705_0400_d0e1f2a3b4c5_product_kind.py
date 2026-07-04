"""product.kind — 'course' | 'event' (events are first-class products now)

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2026-07-05 04:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d0e1f2a3b4c5"
down_revision = "c9d0e1f2a3b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("product")}
    if "kind" not in cols:
        op.add_column(
            "product",
            sa.Column("kind", sa.String(), nullable=False, server_default="course"),
        )


def downgrade() -> None:
    op.drop_column("product", "kind")
