"""ad_creative_map + ad_insight_daily — join our leads to real Meta ad spend

Two tables with deliberately different refresh semantics:

* ad_creative_map is IMMUTABLE per row — an adcreative's instagram_permalink_url never
  changes, so a (media_pk → ad) row is true forever. It is never re-synced, only extended
  when a new ad appears (incrementally, or on demand the moment an unmapped lead lands).
* ad_insight_daily is a rolling ETL cache, one row per (ad, day). Meta revises attribution
  for ~7 days, so recent days get re-fetched and older days are frozen. Day granularity is
  what lets any date range on the reports panel be a local SUM instead of a Graph call.

Additive only — nothing reads or writes these tables until the sync job is enabled.

Revision ID: a6b7c8d9e0f1
Revises: f4a5b6c7d8e0
Create Date: 2026-07-15 12:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a6b7c8d9e0f1"
down_revision = "f4a5b6c7d8e0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "ad_creative_map" not in tables:
        op.create_table(
            "ad_creative_map",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("branch_id", sa.Integer(), sa.ForeignKey("branch.id"), nullable=False),
            # IG media pk from instagrapi — the join key. String, not BigInteger: it arrives
            # as text and channel_thread.ad_media_id is a VARCHAR too, so keep them comparable.
            sa.Column("media_pk", sa.String(), nullable=False),
            sa.Column("shortcode", sa.String(), nullable=False),
            sa.Column("ad_id", sa.String(), nullable=False),
            sa.Column("ad_name", sa.String(), nullable=True),
            sa.Column("adset_id", sa.String(), nullable=True),
            sa.Column("adset_name", sa.String(), nullable=True),
            sa.Column("campaign_id", sa.String(), nullable=True),
            sa.Column("campaign_name", sa.String(), nullable=True),
            sa.Column("objective", sa.String(), nullable=True),
            sa.Column("synced_at", sa.DateTime(), nullable=False),
            # Inline, not op.create_unique_constraint(): SQLite has no ALTER ADD CONSTRAINT,
            # so the standalone call raises NotImplementedError and breaks the test suite's
            # on-SQLite alembic run. Inline works on both backends.
            sa.UniqueConstraint("branch_id", "media_pk", name="uq_admap_branch_media"),
        )
        op.create_index("ix_ad_creative_map_ad_id", "ad_creative_map", ["ad_id"])
        op.create_index("ix_ad_creative_map_media_pk", "ad_creative_map", ["media_pk"])
    if "ad_insight_daily" not in tables:
        op.create_table(
            "ad_insight_daily",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("branch_id", sa.Integer(), sa.ForeignKey("branch.id"), nullable=False),
            sa.Column("ad_id", sa.String(), nullable=False),
            sa.Column("day", sa.Date(), nullable=False),
            # Numeric, not Float: money. Graph returns spend as a decimal string.
            sa.Column("spend", sa.Numeric(12, 2), nullable=False, server_default="0"),
            sa.Column("impressions", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("reach", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("clicks", sa.Integer(), nullable=False, server_default="0"),
            # Meta's own conversation-quality ladder — the counterpart to our lead stages.
            sa.Column("conv_started", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("conv_depth_2", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("conv_depth_3", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("conv_depth_5", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("blocks", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("synced_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint(
                "branch_id", "ad_id", "day", name="uq_adinsight_branch_ad_day"),
        )
        op.create_index("ix_ad_insight_daily_day", "ad_insight_daily", ["day"])
        op.create_index("ix_ad_insight_daily_ad_id", "ad_insight_daily", ["ad_id"])


def downgrade() -> None:
    op.drop_table("ad_insight_daily")
    op.drop_table("ad_creative_map")
