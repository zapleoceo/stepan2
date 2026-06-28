"""bigint_telegram_id

Revision ID: 5dc339ca975e
Revises: 5ff069fc75d8
Create Date: 2026-06-28 14:19:58.288701

Telegram user IDs exceed int32 max (2^31-1). Alter app_user.telegram_id
from INTEGER to BIGINT so large Telegram IDs are stored without overflow.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '5dc339ca975e'
down_revision: str | None = '5ff069fc75d8'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # batch_alter_table is portable: recreates table on SQLite, ALTER COLUMN on Postgres
    with op.batch_alter_table('app_user') as batch_op:
        batch_op.alter_column(
            'telegram_id',
            existing_type=sa.Integer(),
            type_=sa.BigInteger(),
            existing_nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table('app_user') as batch_op:
        batch_op.alter_column(
            'telegram_id',
            existing_type=sa.BigInteger(),
            type_=sa.Integer(),
            existing_nullable=False,
        )
