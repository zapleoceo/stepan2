"""lead.manager_only: the funnel predicate stops being a subquery per row

"Is this one of Stepan's leads, or somebody the manager was already talking to?" was
answered on every read by two correlated subqueries over channel_thread. `lead` already
takes 573 thousand sequential scans reading two billion rows in the life of this database;
adding per-row subqueries to the funnel counters, the inbox and every report is the wrong
direction, and the answer only changes when a thread is attached.

Backfilled: a lead is manager-only when it HAS threads and every one of them is on a
read-only channel. Having no thread at all is neither — that lead is simply new.

Revision ID: mgronly001
Revises: thrcnt001
Create Date: 2026-08-07 12:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "mgronly001"
down_revision = "thrcnt001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("lead", sa.Column("manager_only", sa.Boolean(), nullable=False,
                                    server_default=sa.false()))
    op.create_index("ix_lead_manager_only", "lead", ["manager_only"])
    op.execute(
        "UPDATE lead SET manager_only = true WHERE"
        " EXISTS (SELECT 1 FROM channel_thread ct WHERE ct.lead_id = lead.id)"
        " AND NOT EXISTS (SELECT 1 FROM channel_thread ct"
        "                 JOIN channel c ON c.id = ct.channel_id"
        "                 WHERE ct.lead_id = lead.id AND NOT c.read_only)"
    )
    # The same import that created those leads stamped them with the hour it ran, so every
    # report keyed on arrival date shows a spike on the day of the backfill instead of the
    # months the conversations actually spanned. Their first message is the honest answer.
    op.execute(
        "UPDATE lead SET created_at = COALESCE(("
        "  SELECT MIN(m.occurred_at) FROM message m"
        "  JOIN channel_thread ct ON ct.id = m.thread_id"
        "  WHERE ct.lead_id = lead.id), created_at)"
        " WHERE EXISTS (SELECT 1 FROM channel_thread ct"
        "               JOIN message m ON m.thread_id = ct.id"
        "               WHERE ct.lead_id = lead.id AND m.occurred_at < lead.created_at)"
    )


def downgrade() -> None:
    op.drop_index("ix_lead_manager_only", table_name="lead")
    op.drop_column("lead", "manager_only")
