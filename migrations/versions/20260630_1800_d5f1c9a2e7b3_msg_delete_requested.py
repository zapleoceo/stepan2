"""add message.delete_requested — IG-unsend request flag

Revision ID: d5f1c9a2e7b3
Revises: c3e8f2a9d4b1
Create Date: 2026-06-30 18:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d5f1c9a2e7b3"
down_revision = "c3e8f2a9d4b1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("message")}
    if "delete_requested" not in cols:
        op.add_column(
            "message",
            sa.Column("delete_requested", sa.Boolean(), nullable=False,
                      server_default=sa.false()),
        )
        op.create_index("ix_message_delete_requested", "message", ["delete_requested"])


def downgrade() -> None:
    op.drop_index("ix_message_delete_requested", table_name="message")
    op.drop_column("message", "delete_requested")
