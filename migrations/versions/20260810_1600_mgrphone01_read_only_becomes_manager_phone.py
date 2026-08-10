"""channel.read_only → channel.manager_phone: a fact about the handset, not a permission

read_only did two jobs under one name: "the bot must not write here" and "nobody from our side
may write here at all". The second one broke the first: 295 of the 302 leads on a manager's
number exist ONLY there, so handing one back to Stepan could never do anything — the manager
flipped the switch and nothing ever answered.

What the bot may do is a per-LEAD question, and it already has an answer: the stage the manager
sets and the bot switch that moves with it (domain/funnel.py). The channel only ever knew one
thing worth knowing — whose phone this is. Renamed to say that, and it stops gating sends.

Revision ID: mgrphone01
Revises: mgronly001
Create Date: 2026-08-10 16:00:00
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "mgrphone01"
down_revision = "mgronly001"
branch_labels = None
depends_on = None


# Переименование требует AccessExclusiveLock на `channel`, а таблицу в этот момент читает
# работающее приложение — старый код ещё обслуживает запросы, пока миграция идёт (так и
# задумано в deploy.yml). Первый выкат поймал на этом дедлок: миграция стадий успевала взять
# строки `lead`, приложение держало `channel` и ждало `lead` — цикл.
#
# Поэтому эта миграция стоит ПЕРВОЙ в цепочке: лок берётся, когда мы ещё ничего не держим, и
# конкурирующему запросу остаётся просто подождать. lock_timeout не даёт подвесить сайт, если
# кто-то читает долго, а повтор переживает единичное совпадение.
_ATTEMPTS = 5


def upgrade() -> None:
    conn = op.get_bind()
    # Локи и lock_timeout — постгресовые; на SQLite (где эту цепочку прогоняет test_infra)
    # ни того, ни другого нет, и конкурировать там не с кем.
    if conn.dialect.name != "postgresql":
        op.alter_column("channel", "read_only", new_column_name="manager_phone")
        return
    for attempt in range(1, _ATTEMPTS + 1):
        conn.execute(text("SET LOCAL lock_timeout = '4s'"))
        try:
            conn.execute(text("LOCK TABLE channel IN ACCESS EXCLUSIVE MODE"))
        except Exception:  # noqa: BLE001 — занято живым запросом, пробуем ещё
            if attempt == _ATTEMPTS:
                raise
            conn.rollback()
            continue
        break
    conn.execute(text("SET LOCAL lock_timeout = 0"))
    op.alter_column("channel", "read_only", new_column_name="manager_phone")


def downgrade() -> None:
    op.alter_column("channel", "manager_phone", new_column_name="read_only")
