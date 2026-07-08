"""lead.manager_note — per-lead manager override note, injected into the reply prompt

Revision ID: c5d6e7f8a9b0
Revises: b4c5d6e7f8a9
Create Date: 2026-07-08 11:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c5d6e7f8a9b0"
down_revision = "b4c5d6e7f8a9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("lead")}
    if "manager_note" not in cols:
        op.add_column("lead", sa.Column("manager_note", sa.Text(), nullable=True))
    if "manager_note_by" not in cols:
        op.add_column("lead", sa.Column("manager_note_by", sa.String(), nullable=True))
    if "manager_note_at" not in cols:
        op.add_column("lead", sa.Column("manager_note_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("lead", "manager_note_at")
    op.drop_column("lead", "manager_note_by")
    op.drop_column("lead", "manager_note")
