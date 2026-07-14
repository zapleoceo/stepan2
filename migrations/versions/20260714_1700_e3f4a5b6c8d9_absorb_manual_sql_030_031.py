"""Absorb the two manual SQL files (030, 031) into the Alembic chain.

migrations/030_ig_profile_and_ad_fields.sql and migrations/031_lead_audience.sql were
applied to production by hand and lived OUTSIDE Alembic — a fresh database built by
`alembic upgrade head` would lack these columns and crash on first query. This revision
makes the chain self-sufficient; every step is guarded by an inspector check, so on the
production database (columns already present) it is a clean no-op. The manual files are
deleted in the same commit.

Revision ID: e3f4a5b6c8d9
Revises: d2e3f4a5b6c7
Create Date: 2026-07-14 17:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e3f4a5b6c8d9"
down_revision = "d2e3f4a5b6c7"
branch_labels = None
depends_on = None

_LEAD_COLS = (
    ("ig_username", sa.String()),
    ("ig_user_id", sa.String()),
    ("avatar_url", sa.Text()),
    ("audience", sa.String(16)),
)
_THREAD_COLS = (
    ("lead_source", sa.String(40)),
    ("ad_id", sa.String(40)),
    ("ad_media_id", sa.String(40)),
    ("ad_preview_url", sa.Text()),
)


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    lead_cols = {c["name"] for c in inspector.get_columns("lead")}
    thread_cols = {c["name"] for c in inspector.get_columns("channel_thread")}

    for name, type_ in _LEAD_COLS:
        if name not in lead_cols:
            op.add_column("lead", sa.Column(name, type_, nullable=True))
    for name, type_ in _THREAD_COLS:
        if name not in thread_cols:
            op.add_column("channel_thread", sa.Column(name, type_, nullable=True))

    # 031 data step: move school-age leads onto the audience axis and reset their
    # never-captured temperature. Idempotent by predicate — after the first run no row
    # has lead_type='student', so re-running is a no-op (already true on production).
    op.execute(
        "UPDATE lead SET audience = 'student', lead_type = 'unclear' "
        "WHERE lead_type = 'student'"
    )


def downgrade() -> None:
    for name, _ in _THREAD_COLS:
        op.drop_column("channel_thread", name)
    for name, _ in _LEAD_COLS:
        op.drop_column("lead", name)
