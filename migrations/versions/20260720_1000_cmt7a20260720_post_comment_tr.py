"""post_comment translation cache — text_tr / reply_tr (JSON {lang: translation})

The comments panel translates each question and reply to the UI language via chat:fast and
caches the result so a re-render never re-bills a translation (same pattern as message.tr_text
and lead.needs_tr).

Revision ID: a1b2c3d4e5f6
Revises: c8d9e0f1a2b3
Create Date: 2026-07-20 10:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "cmt7a20260720"
down_revision = "c8d9e0f1a2b3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cols = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("post_comment")}
    if "text_tr" not in cols:
        op.add_column("post_comment", sa.Column("text_tr", sa.String(), nullable=True))
    if "reply_tr" not in cols:
        op.add_column("post_comment", sa.Column("reply_tr", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("post_comment", "reply_tr")
    op.drop_column("post_comment", "text_tr")
