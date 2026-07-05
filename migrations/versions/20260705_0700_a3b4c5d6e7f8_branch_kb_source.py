"""branch.kb_source_branch_id — link a branch's KB to another branch's

Revision ID: a3b4c5d6e7f8
Revises: f2a3b4c5d6e7
Create Date: 2026-07-05 07:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a3b4c5d6e7f8"
down_revision = "f2a3b4c5d6e7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("branch")}
    if "kb_source_branch_id" not in cols:
        op.add_column("branch", sa.Column("kb_source_branch_id", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("branch", "kb_source_branch_id")
