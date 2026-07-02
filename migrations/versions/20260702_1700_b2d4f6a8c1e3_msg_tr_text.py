"""cache message translations: message.tr_text

Revision ID: b2d4f6a8c1e3
Revises: a1c2e3f4b5d6
Create Date: 2026-07-02 17:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b2d4f6a8c1e3"
down_revision = "a1c2e3f4b5d6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "tr_text" not in {c["name"] for c in inspector.get_columns("message")}:
        op.add_column("message", sa.Column("tr_text", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("message", "tr_text")
