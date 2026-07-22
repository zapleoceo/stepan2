"""message.is_ad_referral — structural ad-click marker, not a text guess

IG tags SOME inbound items with ad_id/ad_media_id/lead_source='ad_clicktomsg' (Meta's own
click-to-message referral metadata). ingest._store now stamps this per-message instead of
only aggregating it onto the thread — so situations.lead_spoke_own_words/unseen_media_in_turn
and critic.py can skip an ad-click message's text ENTIRELY (it's the ad's own caption/CTA
prefill, never the lead's own words, however it happens to be phrased) instead of guessing via
text-pattern regexes that only catch phrasings already seen once (thread 4849: an
unrecognized ad caption slipped past every existing regex and got answered as if the lead had
personally said it).

Additive + idempotent. Existing rows default to false — historical dialogs keep relying on
the regex fallbacks, which stay in place unchanged; only new ingestion benefits from here on.

Revision ID: adf4849a1234
Revises: dropkc20260720
Create Date: 2026-07-22 05:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "adf4849a1234"
down_revision = "dropkc20260720"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cols = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("message")}
    if "is_ad_referral" not in cols:
        op.add_column("message", sa.Column(
            "is_ad_referral", sa.Boolean(), nullable=False, server_default="false"))
        op.create_index("ix_message_is_ad_referral", "message", ["is_ad_referral"])


def downgrade() -> None:
    op.drop_index("ix_message_is_ad_referral", table_name="message")
    op.drop_column("message", "is_ad_referral")
