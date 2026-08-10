"""manager_alert.tg_message_id — чтобы алерт можно было переписать, а не продублировать

Алерты по одному лиду шли лентой: каждое событие добавляло сообщение, и топик переставали
читать. Чтобы вместо второй карточки переписывать первую (и убирать отработанную), нужен id
сообщения в Telegram — раньше он нигде не сохранялся, send() отдавал только статус.

Revision ID: alertmsg01
Revises: mgrphact01
Create Date: 2026-08-10 20:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "alertmsg01"
down_revision = "mgrphact01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("manager_alert", sa.Column("tg_message_id", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("manager_alert", "tg_message_id")
