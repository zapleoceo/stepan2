"""crm_lead_state: record the event a lead is booked onto, not only the contract

A booking is the conversion that lands BEFORE a contract, and nothing on our side could see
it. Checked against the live CRM on 2026-08-05: eight leads Stepan worked through in July are
all registered on "VIBE CODING DEMO 08/08/2026", and not one of them has a contract — so the
reports, which count deals alone, showed those conversations as having achieved nothing.

The data was always in the history the read-gate already fetches; only the boolean survived
parsing. These two columns give the reports something countable, next to deal_won.

Nullable and unbackfilled on purpose: the gate refreshes a lead's state on its own cadence, so
the columns fill as leads are touched. Backfilling would mean one CRM round-trip per lead
against the slowest system we integrate with, to learn something the next refresh learns free.

Revision ID: crmev00001
Revises: prcm000001
Create Date: 2026-08-05 14:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "crmev00001"
down_revision = "prcm000001"
branch_labels = None
depends_on = None

_TABLE = "crm_lead_state"
_INDEX = "ix_crm_lead_state_event_at"


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column("event_name", sa.String(), nullable=True))
    op.add_column(_TABLE, sa.Column("event_at", sa.DateTime(), nullable=True))
    # Indexed because the reports filter on it by period ("booked onto something upcoming"),
    # the same shape deal_won_at is queried in.
    op.create_index(_INDEX, _TABLE, ["event_at"])


def downgrade() -> None:
    op.drop_index(_INDEX, table_name=_TABLE)
    op.drop_column(_TABLE, "event_at")
    op.drop_column(_TABLE, "event_name")
