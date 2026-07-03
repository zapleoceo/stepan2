"""shared-post link + preview on message: message.link_url, message.preview_url

Revision ID: d6f2a8c4b1e7
Revises: c4e9b1d7a2f5
Create Date: 2026-07-03 06:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d6f2a8c4b1e7"
down_revision = "c4e9b1d7a2f5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("message")}
    if "link_url" not in cols:
        op.add_column("message", sa.Column("link_url", sa.String(), nullable=True))
    if "preview_url" not in cols:
        op.add_column("message", sa.Column("preview_url", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("message", "preview_url")
    op.drop_column("message", "link_url")
