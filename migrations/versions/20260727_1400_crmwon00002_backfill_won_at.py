"""Backfill crm_lead_state.deal_won_at from the raw CRM JSON

crmwon00001 backfilled the boolean but not the date, so every historical win landed
undated. An undated win is invisible to any date-scoped report — and three of the four
rows on production closed months before Stepan ever wrote to that phone, which is exactly
the case the date is there to exclude.

The timestamps carry a +07:00 offset; casting to timestamptz then to timestamp normalises
them to UTC, matching what the ORM writes on every subsequent poll.

Revision ID: crmwon00002
Revises: crmwon00001
Create Date: 2026-07-27 14:00:00
"""
from __future__ import annotations

from alembic import op

revision = "crmwon00002"
down_revision = "crmwon00001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(
        "UPDATE crm_lead_state"
        " SET deal_won_at = ((raw::json ->> 'deal_won_at')::timestamptz AT TIME ZONE 'UTC')"
        " WHERE deal_won_at IS NULL AND raw IS NOT NULL"
        "   AND (raw::json ->> 'deal_won_at') IS NOT NULL"
    )


def downgrade() -> None:
    pass
