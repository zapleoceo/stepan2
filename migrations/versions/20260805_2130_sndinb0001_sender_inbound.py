"""sender_inbound: persist what the CRM sender hands us, instead of an in-process deque

The callback endpoint deduplicated in memory, which its own comment called out as something
that MUST move to the database before any reply goes out. Two reasons it cannot stay:

  * a restart forgets every id, so the first callback after a deploy can be answered twice;
  * nothing can be reconciled against memory. Their side does not retry a failed callback
    (their first spec: "connect_timeout 5с, помилки лише логуються"), so a message that
    arrives while we are restarting is gone — they have it, we never did, and there is no way
    to find out. Victor's answer of 2026-08-05 gives a catch-up method, and catching up means
    comparing their list for a window against what we actually hold.

`external_id` is unique: it is the messenger's own id and the deduplication key on both
sides, so a repeat from the callback and the same message picked up by the catch-up collide
in the index rather than becoming two rows.

Their project/branch ids are stored as text and NOT mapped to our branches here. The mapping
does not exist yet — we have the alias for Jakarta (`crm`, branch 435) but not the numeric
list — and inventing one would quietly point a lead at the wrong catalogue.

Revision ID: sndinb0001
Revises: obxdlv0001
Create Date: 2026-08-05 21:30:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "sndinb0001"
down_revision = "obxdlv0001"
branch_labels = None
depends_on = None

_TABLE = "sender_inbound"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("external_id", sa.String(), nullable=False),
        sa.Column("project_id", sa.String(), nullable=True),
        sa.Column("branch_ref", sa.String(), nullable=True),
        sa.Column("conversation_id", sa.String(), nullable=True),
        sa.Column("chat_id", sa.String(), nullable=True),
        sa.Column("sender_message_id", sa.String(), nullable=True),
        sa.Column("phone", sa.String(), nullable=True),
        sa.Column("from_name", sa.String(), nullable=True),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("attachment", sa.String(), nullable=True),
        sa.Column("channel_name", sa.String(), nullable=True),
        sa.Column("direction", sa.String(), nullable=False, server_default="in"),
        sa.Column("arrived_via", sa.String(), nullable=False, server_default="callback"),
        sa.Column("received_at", sa.DateTime(), nullable=False),
        sa.Column("processed_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_sender_inbound_external_id", _TABLE, ["external_id"], unique=True)
    op.create_index("ix_sender_inbound_conversation_id", _TABLE, ["conversation_id"])
    op.create_index("ix_sender_inbound_phone", _TABLE, ["phone"])
    op.create_index("ix_sender_inbound_arrived_via", _TABLE, ["arrived_via"])
    op.create_index("ix_sender_inbound_received_at", _TABLE, ["received_at"])
    op.create_index("ix_sender_inbound_processed_at", _TABLE, ["processed_at"])


def downgrade() -> None:
    op.drop_table(_TABLE)
