"""persona library — versioned seller personas + per-branch selection/addendum/favorites

Additive only (three new tables); nothing on the live reply path changes. The reply prompt
keeps using the branch's persona_core KB doc until a later phase wires the library in.

Revision ID: c1d2e3f4a5b6
Revises: a9b0c1d2e3f4
Create Date: 2026-07-13 12:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c1d2e3f4a5b6"
down_revision = "a9b0c1d2e3f4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())

    if "persona" not in tables:
        op.create_table(
            "persona",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("slug", sa.String(), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("version", sa.String(), nullable=False, server_default="1.0"),
            sa.Column("author_user_id", sa.Integer(),
                      sa.ForeignKey("app_user.id"), nullable=True),
            sa.Column("author_name", sa.String(), nullable=False, server_default=""),
            sa.Column("author_contact", sa.String(), nullable=False, server_default=""),
            sa.Column("summary", sa.String(), nullable=False, server_default=""),
            sa.Column("content", sa.Text(), nullable=False, server_default=""),
            sa.Column("lang", sa.String(), nullable=False, server_default="en"),
            sa.Column("country", sa.String(), nullable=False, server_default=""),
            sa.Column("status", sa.String(), nullable=False, server_default="published"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("slug", "version", name="uq_persona_slug_version"),
        )
        op.create_index("ix_persona_slug", "persona", ["slug"])

    if "branch_persona" not in tables:
        op.create_table(
            "branch_persona",
            sa.Column("branch_id", sa.Integer(), sa.ForeignKey("branch.id"),
                      primary_key=True),
            sa.Column("persona_id", sa.Integer(), sa.ForeignKey("persona.id"), nullable=True),
            sa.Column("addendum", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )

    if "persona_favorite" not in tables:
        op.create_table(
            "persona_favorite",
            sa.Column("branch_id", sa.Integer(), sa.ForeignKey("branch.id"),
                      primary_key=True),
            sa.Column("persona_id", sa.Integer(), sa.ForeignKey("persona.id"),
                      primary_key=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )


def downgrade() -> None:
    op.drop_table("persona_favorite")
    op.drop_table("branch_persona")
    op.drop_index("ix_persona_slug", table_name="persona")
    op.drop_table("persona")
