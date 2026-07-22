"""message.is_ad_referral marks only the FIRST inbound of a thread

Meta's referral metadata (ad_id / ad_media_id / lead_source) is THREAD-level and is returned
again on every later message, so ingest stamped everything the lead typed after the tap as
"the ad's words, not theirs". On branch 1 that left 33 threads where real typed questions were
invisible to the answer gate, ignored by the critic, and never recorded in the dossier.

Ingest now flags only the first inbound of a thread. This clears the flag on every LATER
inbound already stored, keeping the earliest one per thread — the actual prefill.

Revision ID: ad1f1rst0nly
Revises: d0551e12v3a1
Create Date: 2026-07-23 03:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "ad1f1rst0nly"
down_revision = "d0551e12v3a1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cols = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("message")}
    if "is_ad_referral" not in cols:
        return
    op.execute("""
        UPDATE message SET is_ad_referral = false
         WHERE is_ad_referral = true
           AND direction = 'in'
           AND id NOT IN (
               SELECT MIN(id) FROM message
                WHERE direction = 'in' AND is_ad_referral = true
                GROUP BY thread_id)
    """)


def downgrade() -> None:
    """One-way: which later messages carried the (thread-level) referral is not recoverable,
    and re-marking them would restore the defect."""
