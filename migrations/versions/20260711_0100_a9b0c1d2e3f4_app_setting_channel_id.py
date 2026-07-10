"""app_setting.channel_id — connector-scope settings tier

Adds a nullable channel_id so a setting can resolve at three levels by increasing
precision: (NULL, NULL)=platform, (branch, NULL)=branch, (branch, channel)=connector.
The old UNIQUE(branch_id, key) is replaced by a COALESCE-based unique index, because a
plain UNIQUE treats NULLs as distinct and would allow duplicate platform/branch rows.

Revision ID: a9b0c1d2e3f4
Revises: f8a9b0c1d2e3
Create Date: 2026-07-11 01:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a9b0c1d2e3f4"
down_revision = "f8a9b0c1d2e3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {c["name"] for c in insp.get_columns("app_setting")}
    uniques = {u["name"] for u in insp.get_unique_constraints("app_setting")}
    indexes = {i["name"] for i in insp.get_indexes("app_setting")}

    # SQLite can't ALTER-drop a named constraint in place; batch recreates the table.
    with op.batch_alter_table("app_setting") as batch:
        if "channel_id" not in cols:
            batch.add_column(sa.Column("channel_id", sa.Integer(), nullable=True))
        if "uq_setting_branch_key" in uniques:
            batch.drop_constraint("uq_setting_branch_key", type_="unique")

    if "ix_app_setting_channel_id" not in indexes:
        op.create_index(
            "ix_app_setting_channel_id", "app_setting", ["channel_id"], unique=False)
    if "uq_setting_scope" not in indexes:
        op.create_index(
            "uq_setting_scope", "app_setting",
            [sa.text("COALESCE(branch_id, 0)"), sa.text("COALESCE(channel_id, 0)"), "key"],
            unique=True)


def downgrade() -> None:
    op.drop_index("uq_setting_scope", table_name="app_setting")
    op.drop_index("ix_app_setting_channel_id", table_name="app_setting")
    with op.batch_alter_table("app_setting") as batch:
        batch.create_unique_constraint("uq_setting_branch_key", ["branch_id", "key"])
        batch.drop_column("channel_id")
