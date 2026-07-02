"""media backfill: media_asset table + message.media_pending flag

Revision ID: f8b3c5d7e9a1
Revises: e7a2b4c6d8f0
Create Date: 2026-07-02 11:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f8b3c5d7e9a1"
down_revision = "e7a2b4c6d8f0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    msg_cols = {c["name"] for c in inspector.get_columns("message")}
    if "media_pending" not in msg_cols:
        op.add_column(
            "message",
            sa.Column("media_pending", sa.Boolean(), nullable=False,
                      server_default=sa.false()),
        )
        op.create_index("ix_message_media_pending", "message", ["media_pending"])

    if "media_asset" not in inspector.get_table_names():
        op.create_table(
            "media_asset",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("branch_id", sa.Integer(), sa.ForeignKey("branch.id"), nullable=False),
            sa.Column("message_id", sa.Integer(), sa.ForeignKey("message.id"), nullable=True),
            sa.Column("kind", sa.String(), nullable=False),
            sa.Column("mime", sa.String(), nullable=True),
            sa.Column("url", sa.String(), nullable=True),
            sa.Column("data", sa.LargeBinary(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_media_asset_branch_id", "media_asset", ["branch_id"])
        op.create_index("ix_media_asset_message_id", "media_asset", ["message_id"])


def downgrade() -> None:
    op.drop_table("media_asset")
    op.drop_index("ix_message_media_pending", table_name="message")
    op.drop_column("message", "media_pending")
