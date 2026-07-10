"""mcp_token.branch_id — scope an MCP token to one branch (NULL = universal)

Revision ID: d6e7f8a9b0c1
Revises: c5d6e7f8a9b0
Create Date: 2026-07-09 18:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d6e7f8a9b0c1"
down_revision = "c5d6e7f8a9b0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("mcp_token")}
    if "branch_id" not in cols:
        op.add_column("mcp_token", sa.Column("branch_id", sa.Integer(), nullable=True))
        op.create_index("ix_mcp_token_branch_id", "mcp_token", ["branch_id"])


def downgrade() -> None:
    op.drop_index("ix_mcp_token_branch_id", table_name="mcp_token")
    op.drop_column("mcp_token", "branch_id")
