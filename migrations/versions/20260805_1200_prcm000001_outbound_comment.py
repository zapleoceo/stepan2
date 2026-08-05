"""outbound_comment — our comments under OTHER people's posts

The reactive comment path answers questions under our own posts and lives in `post_comment`.
This is the other direction: we go to a lead's own feed and leave a line there. Different
dedup key (a post we may comment on once, ever, per channel — not a native comment id we are
answering), different caps, different cost of being wrong.

Rows are written BEFORE anything is posted, including the ones the relevance judge rejects.
That is the point: a table holding only what we sent cannot tell anyone whether the judge is
too strict or too loose, and that threshold is the only knob this mission really has.

Revision ID: prcm000001
Revises: web000002
Create Date: 2026-08-05 12:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "prcm000001"
down_revision = "web000002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "outbound_comment",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("branch_id", sa.Integer(), sa.ForeignKey("branch.id"), nullable=False,
                  index=True),
        sa.Column("channel_id", sa.Integer(), sa.ForeignKey("channel.id"), nullable=False),
        sa.Column("lead_id", sa.Integer(), sa.ForeignKey("lead.id"), nullable=True, index=True),
        sa.Column("media_id", sa.String(), nullable=False, index=True),
        sa.Column("media_permalink", sa.String(), nullable=True),
        sa.Column("media_caption", sa.String(), nullable=True),
        sa.Column("author_pk", sa.String(), nullable=False),
        sa.Column("author_username", sa.String(), nullable=True),
        sa.Column("post_taken_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="pending", index=True),
        sa.Column("relevant", sa.Boolean(), nullable=True),
        sa.Column("skip_reason", sa.String(), nullable=True),
        sa.Column("text", sa.String(), nullable=True),
        sa.Column("external_id", sa.String(), nullable=True),
        sa.Column("text_tr", sa.String(), nullable=True),
        sa.Column("llm_info", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP"), index=True),
        sa.Column("handled_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("channel_id", "media_id", name="uq_outbound_media"),
    )


def downgrade() -> None:
    op.drop_table("outbound_comment")
