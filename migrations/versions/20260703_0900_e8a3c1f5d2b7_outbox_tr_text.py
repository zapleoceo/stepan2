"""cache translation of a queued reply: outbox.tr_text

Revision ID: e8a3c1f5d2b7
Revises: d6f2a8c4b1e7
Create Date: 2026-07-03 09:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e8a3c1f5d2b7"
down_revision = "d6f2a8c4b1e7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "tr_text" not in {c["name"] for c in inspector.get_columns("outbox")}:
        op.add_column("outbox", sa.Column("tr_text", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("outbox", "tr_text")
