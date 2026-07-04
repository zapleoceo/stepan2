"""perf indexes: outbox sent-cap counting, message dialog/thread-list, stage_event lookup

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-07-04 23:50:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e5f6a7b8c9d0"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


def _indexes(bind, table: str) -> set[str]:
    return {i["name"] for i in sa.inspect(bind).get_indexes(table)}


def upgrade() -> None:
    bind = op.get_bind()

    if "ix_outbox_sent" not in _indexes(bind, "outbox"):
        op.create_index(
            "ix_outbox_sent",
            "outbox",
            ["branch_id", "sent_at"],
            postgresql_where=sa.text("status='sent'"),
            sqlite_where=sa.text("status='sent'"),
        )

    if "ix_message_thread_recent" not in _indexes(bind, "message"):
        # plain composite (no DESC) — Postgres scans it backwards for the
        # ORDER BY occurred_at DESC, id DESC LIMIT 1/40 queries
        op.create_index(
            "ix_message_thread_recent", "message", ["thread_id", "occurred_at", "id"]
        )

    if "ix_stage_event_thread_id" not in _indexes(bind, "stage_event"):
        op.create_index("ix_stage_event_thread_id", "stage_event", ["thread_id"])


def downgrade() -> None:
    bind = op.get_bind()

    if "ix_stage_event_thread_id" in _indexes(bind, "stage_event"):
        op.drop_index("ix_stage_event_thread_id", table_name="stage_event")
    if "ix_message_thread_recent" in _indexes(bind, "message"):
        op.drop_index("ix_message_thread_recent", table_name="message")
    if "ix_outbox_sent" in _indexes(bind, "outbox"):
        op.drop_index("ix_outbox_sent", table_name="outbox")
