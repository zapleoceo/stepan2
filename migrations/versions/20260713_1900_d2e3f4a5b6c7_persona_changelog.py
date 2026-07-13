"""persona.changelog — per-version 'what changed' note for the readable version history

Additive: a nullable-with-default text column. No behaviour change.

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
Create Date: 2026-07-13 19:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d2e3f4a5b6c7"
down_revision = "c1d2e3f4a5b6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cols = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("persona")}
    if "changelog" not in cols:
        op.add_column("persona", sa.Column(
            "changelog", sa.Text(), nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("persona", "changelog")
