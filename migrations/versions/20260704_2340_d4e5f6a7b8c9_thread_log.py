"""thread_log — chat-window-visible technical log (context clear/load, etc.)

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-07-04 23:40:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d4e5f6a7b8c9"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    now = sa.text("now()") if bind.dialect.name != "sqlite" else sa.text("CURRENT_TIMESTAMP")
    if "thread_log" not in sa.inspect(bind).get_table_names():
        op.create_table(
            "thread_log",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("branch_id", sa.Integer(), sa.ForeignKey("branch.id"), nullable=False),
            sa.Column("thread_id", sa.Integer(), sa.ForeignKey("channel_thread.id"),
                      nullable=False),
            sa.Column("kind", sa.String(), nullable=False),
            sa.Column("detail", sa.Text(), nullable=True),
            sa.Column("actor", sa.String(), nullable=False, server_default="manager"),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=now),
        )
        op.create_index("ix_thread_log_thread", "thread_log", ["thread_id", "id"])


def downgrade() -> None:
    bind = op.get_bind()
    if "thread_log" in sa.inspect(bind).get_table_names():
        op.drop_table("thread_log")
