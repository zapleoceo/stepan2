"""channel.read_only → channel.manager_phone: a fact about the handset, not a permission

read_only did two jobs under one name: "the bot must not write here" and "nobody from our side
may write here at all". The second one broke the first: 295 of the 302 leads on a manager's
number exist ONLY there, so handing one back to Stepan could never do anything — the manager
flipped the switch and nothing ever answered.

What the bot may do is a per-LEAD question, and it already has an answer: the stage the manager
sets and the bot switch that moves with it (domain/funnel.py). The channel only ever knew one
thing worth knowing — whose phone this is. Renamed to say that, and it stops gating sends.

Revision ID: mgrphone01
Revises: mgrstage01
Create Date: 2026-08-10 16:00:00
"""
from __future__ import annotations

from alembic import op

revision = "mgrphone01"
down_revision = "mgrstage01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("channel", "read_only", new_column_name="manager_phone")


def downgrade() -> None:
    op.alter_column("channel", "manager_phone", new_column_name="read_only")
