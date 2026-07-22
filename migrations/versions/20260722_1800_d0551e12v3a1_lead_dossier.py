"""lead.dossier — the v3 seller's working memory

The v2 `needs` JSON carried only jobs/pains/gains and leaked in four ways (written on live
replies only, objections wholesale replaced each turn, a word-overlap filter deleting rephrased
pains, nothing recording what the bot had already said). The dossier replaces it with a fuller
structure: who the lead is, why they came, who decides, money signals, objections WITH a
status, and what has already been spent (products named, cases used, arguments used).

Additive and idempotent, and deliberately does NOT touch `needs`: v2 keeps reading and writing
its own column, so both engines run side by side and flipping reply_engine per branch — in
either direction — never costs a thread its context. A lead that still has only `needs` is
converted to a dossier on read (conversation.dossier.parse_dossier), so nothing needs
backfilling and no in-flight conversation stalls at the switchover.

Revision ID: d0551e12v3a1
Revises: adf4849a1234
Create Date: 2026-07-22 18:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d0551e12v3a1"
down_revision = "adf4849a1234"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cols = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("lead")}
    if "dossier" not in cols:
        op.add_column("lead", sa.Column("dossier", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("lead", "dossier")
