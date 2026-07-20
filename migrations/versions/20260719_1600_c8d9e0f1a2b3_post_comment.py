"""post_comment — comments under our own posts + our replies

New public channel, separate from DMs. Under a post many different people comment, so this is
NOT a ChannelThread (that is bound to one lead). One row = one comment; the unique
(channel_id, external_id) gives dedup across overlapping hourly ingest runs — same trick that
protects `message`.

Revision ID: c8d9e0f1a2b3
Revises: f1a2b3c4d5e6
Create Date: 2026-07-19 16:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c8d9e0f1a2b3"
# Chained onto the committed head (f1a2b3c4d5e6, drop_placeholder_personas): a parallel
# session landed reping+persona migrations after b7c8d9e0f1a2 while this was in flight, so
# pointing at b7c8 would fork alembic into two heads.
down_revision = "f1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "post_comment" in set(sa.inspect(op.get_bind()).get_table_names()):
        return
    op.create_table(
        "post_comment",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("branch_id", sa.Integer(), sa.ForeignKey("branch.id"), nullable=False),
        sa.Column("channel_id", sa.Integer(), sa.ForeignKey("channel.id"), nullable=False),
        sa.Column("external_id", sa.String(), nullable=False),
        sa.Column("media_id", sa.String(), nullable=False),
        sa.Column("media_caption", sa.String(), nullable=True),
        sa.Column("media_permalink", sa.String(), nullable=True),
        sa.Column("author_username", sa.String(), nullable=True),
        sa.Column("author_pk", sa.String(), nullable=True),
        sa.Column("text", sa.String(), nullable=False, server_default=""),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("skip_reason", sa.String(), nullable=True),
        sa.Column("reply_text", sa.String(), nullable=True),
        sa.Column("reply_external_id", sa.String(), nullable=True),
        sa.Column("llm_info", sa.String(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("handled_at", sa.DateTime(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        # Inline, not op.create_unique_constraint(): SQLite has no ALTER ADD CONSTRAINT and
        # the standalone call breaks the on-SQLite alembic run in tests.
        sa.UniqueConstraint("channel_id", "external_id", name="uq_comment_ext"),
    )
    op.create_index("ix_post_comment_branch_id", "post_comment", ["branch_id"])
    op.create_index("ix_post_comment_media_id", "post_comment", ["media_id"])
    op.create_index("ix_post_comment_status", "post_comment", ["status"])
    op.create_index("ix_post_comment_created_at", "post_comment", ["created_at"])


def downgrade() -> None:
    op.drop_table("post_comment")
