"""retire the website-demo persona left behind in live libraries

`SEED_PERSONAS["website-demo"]` and its seeder are gone from the source, but deleting code does
not delete rows: `ensure_seeded` ran on every persona-library page view, so every install where
anybody opened that page holds the row. Without this migration the operator's library shows the
stale "Stepan (website demo)" v1.2 next to the S6 prompt-library entry the site now really
sells with — the drifted duplicate this release exists to remove, still on screen.

Retired, not deleted. `list_personas` filters on `status = 'published'`, so retiring takes the
card off the grid, while an operator who edited that text keeps it (the detail page and the
version history still resolve). Deleting would destroy work this migration cannot see, to save
one row.

Idempotent and reversible-ish: downgrade republishes what it retired, which is wrong only for a
row somebody retired by hand first — and re-retiring is one click.

Revision ID: web000002
Revises: web000001
Create Date: 2026-08-05 10:10:00
"""
from __future__ import annotations

from alembic import op

revision = "web000002"
down_revision = "web000001"
branch_labels = None
depends_on = None

_SLUG = "website-demo"


def upgrade() -> None:
    op.execute(
        "UPDATE persona SET status = 'retired' "  # noqa: S608 — static slug
        f"WHERE slug = '{_SLUG}' AND status = 'published'")


def downgrade() -> None:
    op.execute(
        "UPDATE persona SET status = 'published' "  # noqa: S608 — static slug
        f"WHERE slug = '{_SLUG}' AND status = 'retired'")
