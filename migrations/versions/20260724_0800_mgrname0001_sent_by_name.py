"""outbox/message.sent_by_name — which MANAGER wrote a manual reply

Every manager-sent bubble rendered as the generic "менеджер": the dashboard knows the
signed-in user's name at send time, but nothing carried it onto the outbox row or the
recorded message, so a multi-manager branch couldn't tell who said what to a lead. The name
is stamped at enqueue (chat_send → Outbox.sent_by_name), copied onto the Message row when
the send completes, and shown in the bubble. Sends made from the IG app directly arrive via
ingest with no session identity — those stay on the generic label.

Additive and idempotent.

Revision ID: mgrname0001
Revises: rev0k3d7omb
Create Date: 2026-07-24 08:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "mgrname0001"
down_revision = "rev0k3d7omb"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    for table in ("outbox", "message"):
        cols = {c["name"] for c in sa.inspect(bind).get_columns(table)}
        if "sent_by_name" not in cols:
            op.add_column(table, sa.Column("sent_by_name", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("message", "sent_by_name")
    op.drop_column("outbox", "sent_by_name")
