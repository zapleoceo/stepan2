"""stage machinery + followup counter: lead.agent_enabled/handed_off_at,
channel_thread.followups_sent, stage_event table

Revision ID: c3e8f2a9d4b1
Revises: b7d1a4e8c2f9
Create Date: 2026-06-30 17:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c3e8f2a9d4b1"
down_revision = "b7d1a4e8c2f9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    lead_cols = {c["name"] for c in inspector.get_columns("lead")}
    if "agent_enabled" not in lead_cols:
        op.add_column(
            "lead",
            sa.Column("agent_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        )
    if "handed_off_at" not in lead_cols:
        op.add_column("lead", sa.Column("handed_off_at", sa.DateTime(), nullable=True))

    thread_cols = {c["name"] for c in inspector.get_columns("channel_thread")}
    if "followups_sent" not in thread_cols:
        op.add_column(
            "channel_thread",
            sa.Column("followups_sent", sa.Integer(), nullable=False, server_default="0"),
        )

    if "stage_event" not in inspector.get_table_names():
        op.create_table(
            "stage_event",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("branch_id", sa.Integer(), sa.ForeignKey("branch.id"), nullable=False),
            sa.Column("lead_id", sa.Integer(), sa.ForeignKey("lead.id"), nullable=False),
            sa.Column("thread_id", sa.Integer(), nullable=True),
            sa.Column("from_stage", sa.String(), nullable=False),
            sa.Column("to_stage", sa.String(), nullable=False),
            sa.Column("actor", sa.String(), nullable=False, server_default="bot"),
            sa.Column("reason", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_stage_event_branch_id", "stage_event", ["branch_id"])
        op.create_index("ix_stage_event_lead_id", "stage_event", ["lead_id"])


def downgrade() -> None:
    op.drop_table("stage_event")
    op.drop_column("channel_thread", "followups_sent")
    op.drop_column("lead", "handed_off_at")
    op.drop_column("lead", "agent_enabled")
