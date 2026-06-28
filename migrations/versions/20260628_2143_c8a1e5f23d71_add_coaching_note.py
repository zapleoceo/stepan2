"""add coaching_note table

Revision ID: c8a1e5f23d71
Revises: 5dc339ca975e
Create Date: 2026-06-28 21:43:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c8a1e5f23d71"
down_revision = "5dc339ca975e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "coaching_note" not in inspector.get_table_names():
        op.create_table(
            "coaching_note",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("branch_id", sa.Integer(), nullable=False),
            sa.Column("role", sa.String(12), nullable=False),
            sa.Column("text", sa.Text(), nullable=False),
            sa.Column("active", sa.Boolean(), nullable=False, server_default="true"),
            sa.Column("added_by", sa.String(120), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
            sa.ForeignKeyConstraint(["branch_id"], ["branch.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_coaching_note_branch", "coaching_note", ["branch_id"])


def downgrade() -> None:
    op.drop_index("ix_coaching_note_branch", table_name="coaching_note")
    op.drop_table("coaching_note")
