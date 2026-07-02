"""add llm_spend — per-branch daily LLM cost ledger for the budget gate

Revision ID: b7d1a4e8c2f9
Revises: fa4e91b2c3d5
Create Date: 2026-06-30 16:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b7d1a4e8c2f9"
down_revision = "fa4e91b2c3d5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "llm_spend" in inspector.get_table_names():
        return
    op.create_table(
        "llm_spend",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("branch_id", sa.Integer(), sa.ForeignKey("branch.id"), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("used_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("calls", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("branch_id", "day", name="uq_llm_spend_branch_day"),
    )
    op.create_index("ix_llm_spend_branch_id", "llm_spend", ["branch_id"])
    op.create_index("ix_llm_spend_day", "llm_spend", ["day"])


def downgrade() -> None:
    op.drop_table("llm_spend")
