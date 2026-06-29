"""add coaching_edit table

Revision ID: e3f9c2d84b61
Revises: c8a1e5f23d71
Create Date: 2026-06-29 08:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e3f9c2d84b61"
down_revision = "c8a1e5f23d71"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "coaching_edit" not in inspector.get_table_names():
        op.create_table(
            "coaching_edit",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("branch_id", sa.Integer(), nullable=False),
            sa.Column("request", sa.Text(), nullable=False),
            sa.Column("status", sa.String(20), nullable=False, server_default="proposed"),
            sa.Column("slug", sa.String(80), nullable=True),
            sa.Column("old_text", sa.Text(), nullable=True),
            sa.Column("new_text", sa.Text(), nullable=True),
            sa.Column("summary", sa.Text(), nullable=True),
            sa.Column("added_by", sa.String(120), nullable=True),
            sa.Column("applied_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
            sa.ForeignKeyConstraint(["branch_id"], ["branch.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_coaching_edit_branch", "coaching_edit", ["branch_id"])


def downgrade() -> None:
    op.drop_index("ix_coaching_edit_branch", table_name="coaching_edit")
    op.drop_table("coaching_edit")
