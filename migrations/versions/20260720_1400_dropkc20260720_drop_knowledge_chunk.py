"""drop knowledge_chunk — RAG removed

The reply prompt now loads the whole facts-only KB every turn (persona + facts docs + the
focus card + a compact facts catalog), so there is no retrieval index to maintain. rag.py /
reindex.py / chunking.py and the reindex cron/endpoint are gone; this drops their table.

Revision ID: dropkc20260720
Revises: cmt7a20260720
Create Date: 2026-07-20 14:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "dropkc20260720"
down_revision = "cmt7a20260720"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("knowledge_chunk"):
        op.drop_table("knowledge_chunk")


def downgrade() -> None:
    if not sa.inspect(op.get_bind()).has_table("knowledge_chunk"):
        op.create_table(
            "knowledge_chunk",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("branch_id", sa.Integer(), sa.ForeignKey("branch.id"), index=True),
            sa.Column("source_type", sa.String(), nullable=False, server_default="doc"),
            sa.Column("source_slug", sa.String(), nullable=False, server_default=""),
            sa.Column("title", sa.String(), nullable=False, server_default=""),
            sa.Column("seq", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("text", sa.String(), nullable=False, server_default=""),
            sa.Column("embedding", sa.String(), nullable=False, server_default="[]"),
            sa.Column("created_at", sa.DateTime(), nullable=True),
        )
