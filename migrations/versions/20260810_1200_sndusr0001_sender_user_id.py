"""sender_inbound.sender_user_id: их id клиента, без которого нечем ответить

Колбек присылает `user_id` с первого дня, маршрут его читает — и выбрасывал: в отображении
полей на колонки его не было. На приёме он не нужен, поэтому пропажа ничем себя не выдавала.

Нужен он на ОТПРАВКЕ: в таблице соответствий sender `user_id` → `userId`, и без него ответ
пришлось бы слать без адресата, которого их инструмент ждёт (Виктор, 05.08.2026).

Пусто у всех существующих строк и бэкфилла нет: значение приходит только вместе с новым
колбеком, а восстанавливать его из истории неоткуда.

Revision ID: sndusr0001
Revises: mgronly001
Create Date: 2026-08-10 12:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "sndusr0001"
down_revision = "mgronly001"
branch_labels = None
depends_on = None

_TABLE = "sender_inbound"
_COL = "sender_user_id"


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column(_COL, sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column(_TABLE, _COL)
