"""Притязание менеджерского номера получает свой actor — иначе оно читается как хендофф

mgrstage01 перевёл 302 лида в MANAGER и записал каждому настоящий переход стадии с
actor='system'. Выборка отправки в CRM (push_mcp.fetch_unpushed_handoffs) считает любой
такой переход поводом сказать менеджеру «hand-off, hubungi segera» — и через час после
миграции 22 лида с телефоном стояли в очереди на отправку. Это те самые люди, которых
менеджер и так ведёт у себя в WhatsApp: сообщать ему о них нечего, и именно на такие
повторы менеджеры и жаловались.

Переход остаётся в журнале (без него не видно, откуда лида забрали) — меняется только его
авторство, и предикат окна теперь умеет его отличить.

Revision ID: mgrphact01
Revises: mgrstage01
Create Date: 2026-08-10 18:00:00
"""
from __future__ import annotations

from alembic import op

revision = "mgrphact01"
down_revision = "mgrstage01"
branch_labels = None
depends_on = None

_REASON = "read-only connector: a manager owns this conversation"


def upgrade() -> None:
    op.execute(
        "UPDATE stage_event SET actor = 'manager_phone'"
        f" WHERE reason = '{_REASON}' AND actor = 'system'"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE stage_event SET actor = 'system'"
        f" WHERE reason = '{_REASON}' AND actor = 'manager_phone'"
    )
