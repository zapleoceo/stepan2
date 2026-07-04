"""ad_product_map table + channel_thread.product_source

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-07-05 02:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b8c9d0e1f2a3"
down_revision = "a7b8c9d0e1f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = set(insp.get_table_names())
    if "ad_product_map" not in tables:
        op.create_table(
            "ad_product_map",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("branch_id", sa.Integer(), sa.ForeignKey("branch.id"), nullable=False),
            sa.Column("ad_id", sa.String(), nullable=False),
            sa.Column("product_slug", sa.String(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.Column("updated_by", sa.String(), nullable=True),
            sa.UniqueConstraint("branch_id", "ad_id", name="uq_admap_branch_ad"),
        )
        op.create_index("ix_ad_product_map_branch_id", "ad_product_map", ["branch_id"])
        op.create_index("ix_ad_product_map_ad_id", "ad_product_map", ["ad_id"])
    cols = {c["name"] for c in insp.get_columns("channel_thread")}
    if "product_source" not in cols:
        op.add_column(
            "channel_thread", sa.Column("product_source", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("channel_thread", "product_source")
    op.drop_index("ix_ad_product_map_ad_id", table_name="ad_product_map")
    op.drop_index("ix_ad_product_map_branch_id", table_name="ad_product_map")
    op.drop_table("ad_product_map")
