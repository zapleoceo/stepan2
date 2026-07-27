"""crm_lead_state.deal_won / deal_won_at — the sale, as a queryable fact

The CRM has always answered "did this lead buy?" — deal_won and deal_won_at ride in every
MCP state payload — but our side only ever kept them inside the `raw` JSON blob. A report
cannot count a JSON blob, so the reports panel had no notion of a sale at all: its "won"
column counts leads in stage ready/handed_off, which means "handed to a human", not "paid".

Promoting the two fields to columns is what lets the ad funnel show real deals per ad, and
what lets cost-per-sale be a number rather than a guess.

Backfilled from the JSON already on disk, so the column is correct the moment it exists —
no waiting a full CRM poll cycle for history to reappear.

Revision ID: crmwon00001
Revises: botoff00001
Create Date: 2026-07-27 12:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "crmwon00001"
down_revision = "botoff00001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cols = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("crm_lead_state")}
    if "deal_won" not in cols:
        op.add_column("crm_lead_state", sa.Column(
            "deal_won", sa.Boolean(), nullable=False, server_default=sa.false()))
    if "deal_won_at" not in cols:
        op.add_column("crm_lead_state", sa.Column(
            "deal_won_at", sa.DateTime(), nullable=True))
    # Postgres only: SQLite (tests) starts empty, and json_extract differs enough not to
    # be worth branching for a backfill of rows that do not exist there.
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "UPDATE crm_lead_state SET deal_won = true"
            " WHERE raw IS NOT NULL AND (raw::json ->> 'deal_won') = 'true'"
        )


def downgrade() -> None:
    op.drop_column("crm_lead_state", "deal_won_at")
    op.drop_column("crm_lead_state", "deal_won")
