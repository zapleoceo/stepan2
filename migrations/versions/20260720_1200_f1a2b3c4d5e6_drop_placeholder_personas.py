"""drop the placeholder starter personas from the library

The persona library shipped with three demo/starter personas (consultative-closer,
warm-advisor, fast-mover) — generic placeholder text nobody selected or favorited. The real
library is the imported branch personas plus the built-in website-demo persona (re-seeded by
ensure_seeded). Remove the placeholders and any dangling references to them.

Data-only + idempotent (DELETE ... WHERE slug IN (...)); safe to re-run.

Revision ID: f1a2b3c4d5e6
Revises: d9e0f1a2b3c4
Create Date: 2026-07-20 12:00:00
"""
from __future__ import annotations

from alembic import op

revision = "f1a2b3c4d5e6"
down_revision = "d9e0f1a2b3c4"
branch_labels = None
depends_on = None

_JUNK = ("consultative-closer", "warm-advisor", "fast-mover")
_IN = ", ".join(f"'{s}'" for s in _JUNK)


def upgrade() -> None:
    # children first: clear favorites and any (empty in practice) branch selection pointing
    # at a placeholder, then delete every version row of each placeholder slug.
    op.execute(
        f"DELETE FROM persona_favorite WHERE persona_id IN "  # noqa: S608 — static slug list
        f"(SELECT id FROM persona WHERE slug IN ({_IN}))")
    op.execute(
        f"UPDATE branch_persona SET persona_id = NULL WHERE persona_id IN "  # noqa: S608
        f"(SELECT id FROM persona WHERE slug IN ({_IN}))")
    op.execute(f"DELETE FROM persona WHERE slug IN ({_IN})")  # noqa: S608


def downgrade() -> None:
    # Placeholder junk is not restored on downgrade — recreating fake starter data would be
    # worse than a no-op. ensure_seeded never re-adds these slugs.
    pass
