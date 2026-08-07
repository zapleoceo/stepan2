"""lead.is_merged_into: the same person, found twice

The funnel used to end at the phone number. Stepan takes it, a manager continues on
WhatsApp, and everything after that happened where we could not see — a lead who bought and
a lead who vanished looked identical.

Reading the managers' numbers makes the same person exist twice: once as the thread Stepan
worked, once as the chat the manager worked. The phone says they are one person, and it
arrives late, so the two records are already separate by the time we can tell.

The absorbed row is kept rather than deleted. Its id has been handed out, logged and linked,
and a merge that turns out to be wrong must be readable rather than reconstructed.

Revision ID: leadmrg001
Revises: chanro0001
Create Date: 2026-08-07 07:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "leadmrg001"
down_revision = "chanro0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("lead", sa.Column("is_merged_into", sa.Integer(), nullable=True))
    op.create_index("ix_lead_is_merged_into", "lead", ["is_merged_into"])
    # SQLite cannot add a constraint to an existing table, and the test schema is SQLite.
    # Postgres is where the constraint has to hold; skipping it there would let a merge
    # point at a lead that no longer exists.
    if op.get_bind().dialect.name != "sqlite":
        op.create_foreign_key(
            "fk_lead_is_merged_into", "lead", "lead", ["is_merged_into"], ["id"])


def downgrade() -> None:
    if op.get_bind().dialect.name != "sqlite":
        op.drop_constraint("fk_lead_is_merged_into", "lead", type_="foreignkey")
    op.drop_index("ix_lead_is_merged_into", table_name="lead")
    op.drop_column("lead", "is_merged_into")
