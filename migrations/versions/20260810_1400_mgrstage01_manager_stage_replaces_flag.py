"""The manager's own phone becomes a STAGE, not a flag beside one

lead.manager_only answered "is this one of Stepan's leads?" as a column, because as a pair of
correlated subqueries it cost too much on every read (mgronly001, three days ago). The answer
was right and the column was the right shape for it — but it sat next to a stage that said
something else entirely: 265 of the 302 such leads were stage `new` with the bot switched on,
kept quiet only by a channel filter further downstream.

A lead a manager is already working IS a funnel position, and one that already exists. MANAGER
is outside every funnel-stage list (the counters, follow-ups, reactivation, the reply queue),
it silences the bot through domain.funnel.apply_stage, and — unlike a flag — a manager can move
it back when they are done. So the stage carries it and the column goes.

The subqueries do NOT come back: the funnel predicate is now one indexed comparison
(is_merged_into IS NULL), because membership is decided by the stage the lead already has.

Backfilled: every lead with a thread on a read-only channel moves to MANAGER with the bot off,
whatever stage they were in — that is the same rule ingest now applies on first contact. Their
previous stage is journalled to stage_event so a manager can see what it was.

Revision ID: mgrstage01
Revises: mgrphone01
Create Date: 2026-08-10 14:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "mgrstage01"
down_revision = "mgrphone01"
branch_labels = None
depends_on = None

_HAS_READ_ONLY_THREAD = (
    "EXISTS (SELECT 1 FROM channel_thread ct JOIN channel c ON c.id = ct.channel_id"
    "        WHERE ct.lead_id = lead.id AND c.manager_phone)"
)


def upgrade() -> None:
    # Journal BEFORE the update, while lead.stage still holds where they came from.
    op.execute(
        "INSERT INTO stage_event (branch_id, lead_id, thread_id, from_stage, to_stage,"
        "                         actor, reason, created_at)"
        " SELECT lead.branch_id, lead.id, NULL, lead.stage, 'manager', 'system',"
        # CURRENT_TIMESTAMP, not now(): the migration chain is replayed on SQLite in the
        # infra test, and a Postgres-only function there fails the whole upgrade.
        "        'read-only connector: a manager owns this conversation', CURRENT_TIMESTAMP"
        f" FROM lead WHERE {_HAS_READ_ONLY_THREAD} AND lead.stage <> 'manager'"
    )
    op.execute(
        "UPDATE lead SET stage = 'manager', agent_enabled = false"
        f" WHERE {_HAS_READ_ONLY_THREAD} AND stage <> 'manager'"
    )
    op.drop_index("ix_lead_manager_only", table_name="lead")
    op.drop_column("lead", "manager_only")


def downgrade() -> None:
    """Restores the column and its backfill. The stages are NOT rolled back: a manager may
    have moved a lead on since, and guessing which of those moves was ours would undo theirs."""
    op.add_column("lead", sa.Column("manager_only", sa.Boolean(), nullable=False,
                                    server_default=sa.false()))
    op.create_index("ix_lead_manager_only", "lead", ["manager_only"])
    op.execute(
        "UPDATE lead SET manager_only = true WHERE"
        " EXISTS (SELECT 1 FROM channel_thread ct WHERE ct.lead_id = lead.id)"
        " AND NOT EXISTS (SELECT 1 FROM channel_thread ct"
        "                 JOIN channel c ON c.id = ct.channel_id"
        "                 WHERE ct.lead_id = lead.id AND NOT c.manager_phone)"
    )
