"""broker call log: broker_log

Revision ID: a1b2c3d4e5f6
Revises: e8a3c1f5d2b7
Create Date: 2026-07-03 12:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a1b2c3d4e5f6"
down_revision = "e8a3c1f5d2b7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if "broker_log" in sa.inspect(bind).get_table_names():
        return
    op.create_table(
        "broker_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("request_id", sa.String(), nullable=True),
        sa.Column("branch_id", sa.Integer(), nullable=True),
        sa.Column("thread_id", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(), nullable=True),
        sa.Column("capability", sa.String(), nullable=True),
        sa.Column("provider", sa.String(), nullable=True),
        sa.Column("model", sa.String(), nullable=True),
        sa.Column("tokens_in", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokens_out", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("ok", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_broker_log_created", "broker_log", ["created_at"])
    op.create_index("ix_broker_log_branch", "broker_log", ["branch_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_broker_log_branch", table_name="broker_log")
    op.drop_index("ix_broker_log_created", table_name="broker_log")
    op.drop_table("broker_log")
