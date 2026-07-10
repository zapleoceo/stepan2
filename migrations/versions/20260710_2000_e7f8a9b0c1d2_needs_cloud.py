"""needs cloud — canonical need entities, per-lead tags, classification state, daily history

Revision ID: e7f8a9b0c1d2
Revises: d6e7f8a9b0c1
Create Date: 2026-07-10 20:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e7f8a9b0c1d2"
down_revision = "d6e7f8a9b0c1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())

    if "need_entity" not in tables:
        op.create_table(
            "need_entity",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("branch_id", sa.Integer(), sa.ForeignKey("branch.id"), nullable=False),
            sa.Column("kind", sa.String(), nullable=False),          # jobs | pains | gains
            sa.Column("label", sa.String(), nullable=False),         # canonical, stable
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("branch_id", "kind", "label",
                                name="uq_need_entity_branch_kind_label"),
        )
        op.create_index("ix_need_entity_branch_kind", "need_entity", ["branch_id", "kind"])

    if "lead_need_tag" not in tables:
        op.create_table(
            "lead_need_tag",
            sa.Column("lead_id", sa.Integer(), sa.ForeignKey("lead.id"), nullable=False),
            sa.Column("branch_id", sa.Integer(), sa.ForeignKey("branch.id"), nullable=False),
            sa.Column("kind", sa.String(), nullable=False),
            sa.Column("entity_id", sa.Integer(), sa.ForeignKey("need_entity.id"), nullable=False),
            sa.PrimaryKeyConstraint("lead_id", "kind", "entity_id"),
        )
        op.create_index("ix_lead_need_tag_entity", "lead_need_tag", ["entity_id"])
        op.create_index("ix_lead_need_tag_branch_kind", "lead_need_tag", ["branch_id", "kind"])

    if "need_lead_state" not in tables:
        op.create_table(
            "need_lead_state",
            sa.Column("lead_id", sa.Integer(), sa.ForeignKey("lead.id"), primary_key=True),
            sa.Column("branch_id", sa.Integer(), sa.ForeignKey("branch.id"), nullable=False),
            sa.Column("needs_sha", sa.String(), nullable=False),
            sa.Column("classified_at", sa.DateTime(), nullable=False),
        )

    if "need_agg_snapshot" not in tables:
        op.create_table(
            "need_agg_snapshot",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("branch_id", sa.Integer(), sa.ForeignKey("branch.id"), nullable=False),
            sa.Column("kind", sa.String(), nullable=False),
            sa.Column("entity_id", sa.Integer(), sa.ForeignKey("need_entity.id"), nullable=False),
            sa.Column("snap_date", sa.Date(), nullable=False),
            sa.Column("lead_count", sa.Integer(), nullable=False),
            sa.UniqueConstraint("branch_id", "kind", "entity_id", "snap_date",
                                name="uq_need_snap"),
        )


def downgrade() -> None:
    op.drop_table("need_agg_snapshot")
    op.drop_table("need_lead_state")
    op.drop_table("lead_need_tag")
    op.drop_table("need_entity")
