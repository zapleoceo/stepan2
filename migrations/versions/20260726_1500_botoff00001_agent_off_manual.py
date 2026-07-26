"""lead.agent_off_manual — a human's mute survives the lead's next message

agent_enabled has never recorded WHY it is off, and two very different things set it:

  _to_dormant / undeliverable   — the system parking a lead. Should wake on a fresh inbound.
  the Bot OFF pill in the chat  — a person deciding to take the thread. Must NOT wake.

ingest._revive_bot treats both the same: any inbound flips agent_enabled back to True unless
the lead is blocked or the stage is human-led (ready/handed_off/manager). So a manager muting
a lead in `qualifying` got the bot back the moment that lead wrote again — silently, with no
record that a human had ever intervened, because the toggle writes no audit trail either.

This flag is that record. Set when the pill turns the bot OFF, cleared when it turns it back
ON, and honoured by _revive_bot.

Additive and idempotent. Existing rows default to false, which is correct: every current mute
is either a system park (should revive) or old enough not to matter.

Revision ID: botoff00001
Revises: medjob00001
Create Date: 2026-07-26 15:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "botoff00001"
down_revision = "medjob00001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cols = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("lead")}
    if "agent_off_manual" not in cols:
        op.add_column("lead", sa.Column("agent_off_manual", sa.Boolean(), nullable=False,
                                        server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("lead", "agent_off_manual")
