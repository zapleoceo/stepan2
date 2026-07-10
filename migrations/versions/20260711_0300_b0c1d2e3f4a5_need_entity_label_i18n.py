"""need_entity.label_i18n — cached translations of the canonical label (JSON {en, id})

Revision ID: b0c1d2e3f4a5
Revises: a9b0c1d2e3f4
Create Date: 2026-07-11 03:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b0c1d2e3f4a5"
down_revision = "f8a9b0c1d2e3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("need_entity")}
    if "label_i18n" not in cols:
        op.add_column("need_entity", sa.Column("label_i18n", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("need_entity", "label_i18n")
