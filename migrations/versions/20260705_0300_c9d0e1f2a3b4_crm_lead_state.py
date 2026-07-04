"""crm_lead_state — cached CRM read state per lead (send-gate reads verdict)

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-07-05 03:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c9d0e1f2a3b4"
down_revision = "b8c9d0e1f2a3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if "crm_lead_state" in sa.inspect(bind).get_table_names():
        return
    op.create_table(
        "crm_lead_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("branch_id", sa.Integer(), sa.ForeignKey("branch.id"), nullable=False),
        sa.Column("lead_id", sa.Integer(), sa.ForeignKey("lead.id"), nullable=False),
        sa.Column("exists_in_crm", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(), nullable=True),
        sa.Column("owner", sa.String(), nullable=True),
        sa.Column("verdict", sa.String(), nullable=False, server_default="proceed"),
        sa.Column("reason", sa.String(), nullable=True),
        sa.Column("raw", sa.Text(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
        # inline (not ALTER ADD CONSTRAINT) so the migration also applies on SQLite
        sa.UniqueConstraint("lead_id", name="uq_crm_lead_state_lead_id"),
    )
    op.create_index("ix_crm_lead_state_branch_id", "crm_lead_state", ["branch_id"])
    op.create_index("ix_crm_lead_state_fetched_at", "crm_lead_state", ["fetched_at"])


def downgrade() -> None:
    op.drop_table("crm_lead_state")
