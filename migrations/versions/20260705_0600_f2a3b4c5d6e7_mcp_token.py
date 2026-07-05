"""mcp_token — UI-managed bearer tokens for the MCP connectors (write/read scopes)

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-07-05 06:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f2a3b4c5d6e7"
down_revision = "e1f2a3b4c5d6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if "mcp_token" in sa.inspect(bind).get_table_names():
        return
    op.create_table(
        "mcp_token",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("label", sa.String(), nullable=False),
        sa.Column("scope", sa.String(), nullable=False),
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column("prefix", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("token_hash", name="uq_mcp_token_hash"),
    )
    op.create_index("ix_mcp_token_token_hash", "mcp_token", ["token_hash"])


def downgrade() -> None:
    op.drop_table("mcp_token")
