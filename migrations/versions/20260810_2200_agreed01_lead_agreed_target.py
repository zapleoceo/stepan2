"""lead.agreed_product_slug / agreed_price — согласие принадлежит цели, а не человеку

«Готов» было булевым про лида и не помнило, к чему относится. Тред 3163: 28.07 человек
согласился на демо-ивент за 100 тысяч, 10.08 разговор ушёл на курс за 13 миллионов, лид
сказал только «хочу учить AI для карьеры в 3D» — и был отправлен в CRM как оформляющийся,
а в чат ушло «передаю вашу заявку команде». Заявки не было.

Обратный бэкфилл невозможен: чему именно сказали «да», в истории не записано. Поля
заполняются с первого нового согласия.

Revision ID: agreed01
Revises: alertmsg01
Create Date: 2026-08-10 22:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "agreed01"
down_revision = "alertmsg01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("lead", sa.Column("agreed_product_slug", sa.String(), nullable=True))
    op.add_column("lead", sa.Column("agreed_price", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("lead", "agreed_price")
    op.drop_column("lead", "agreed_product_slug")
