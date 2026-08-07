"""message.tr_lang: which language the cached translation is in

The cache was a single column with no language on it, so it answered every language with
whatever had been asked for first. A bubble translated once while the admin was in English
came back in English to an admin reading Russian — which looks like a translator ignoring
you, not like a cache doing its job.

Left NULL for existing rows on purpose. Those translations were made in whatever language
the branch's admin was using, which is almost always the one being asked for now; trusting
them once is cheaper than re-billing every stored translation to find out. A row that does
turn out to be in the wrong language is replaced the first time it is asked for in another.

Revision ID: trlang0001
Revises: leadmrg001
Create Date: 2026-08-07 09:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "trlang0001"
down_revision = "leadmrg001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("message", sa.Column("tr_lang", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("message", "tr_lang")
