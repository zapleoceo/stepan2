"""KB structure: doc category/order/edited, product edited, lead lang, chunk + revision

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-07-03 15:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b2c3d4e5f6a7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def _cols(bind, table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    now = sa.text("now()") if bind.dialect.name != "sqlite" else sa.text("CURRENT_TIMESTAMP")

    kdoc = _cols(bind, "knowledge_doc")
    if "category" not in kdoc:
        op.add_column("knowledge_doc", sa.Column("category", sa.String(), nullable=True))
    if "sort_order" not in kdoc:
        op.add_column("knowledge_doc",
                      sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"))
    if "updated_at" not in kdoc:
        op.add_column("knowledge_doc",
                      sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=now))
    if "updated_by" not in kdoc:
        op.add_column("knowledge_doc", sa.Column("updated_by", sa.String(), nullable=True))

    prod = _cols(bind, "product")
    if "updated_at" not in prod:
        op.add_column("product",
                      sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=now))
    if "updated_by" not in prod:
        op.add_column("product", sa.Column("updated_by", sa.String(), nullable=True))

    if "preferred_language" not in _cols(bind, "lead"):
        op.add_column("lead", sa.Column("preferred_language", sa.String(), nullable=True))

    tables = set(insp.get_table_names())
    if "knowledge_chunk" not in tables:
        op.create_table(
            "knowledge_chunk",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("branch_id", sa.Integer(), sa.ForeignKey("branch.id"), nullable=False),
            sa.Column("source_type", sa.String(), nullable=False, server_default="doc"),
            sa.Column("source_slug", sa.String(), nullable=False, server_default=""),
            sa.Column("title", sa.String(), nullable=False, server_default=""),
            sa.Column("seq", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("text", sa.Text(), nullable=False, server_default=""),
            sa.Column("embedding", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=now),
        )
        op.create_index("ix_knowledge_chunk_branch", "knowledge_chunk", ["branch_id"])
        op.create_index("ix_knowledge_chunk_source", "knowledge_chunk",
                        ["branch_id", "source_type", "source_slug"])

    if "knowledge_revision" not in tables:
        op.create_table(
            "knowledge_revision",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("branch_id", sa.Integer(), nullable=True),
            sa.Column("entity_type", sa.String(), nullable=False, server_default="doc"),
            sa.Column("slug", sa.String(), nullable=False, server_default=""),
            sa.Column("old_content", sa.Text(), nullable=True),
            sa.Column("new_content", sa.Text(), nullable=False, server_default=""),
            sa.Column("old_len", sa.Integer(), nullable=True),
            sa.Column("new_len", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("actor", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=now),
        )
        op.create_index("ix_knowledge_revision_entity", "knowledge_revision",
                        ["branch_id", "entity_type", "slug", "created_at"])


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())

    if "knowledge_revision" in tables:
        op.drop_table("knowledge_revision")
    if "knowledge_chunk" in tables:
        op.drop_table("knowledge_chunk")

    if "preferred_language" in _cols(bind, "lead"):
        op.drop_column("lead", "preferred_language")

    prod = _cols(bind, "product")
    if "updated_by" in prod:
        op.drop_column("product", "updated_by")
    if "updated_at" in prod:
        op.drop_column("product", "updated_at")

    kdoc = _cols(bind, "knowledge_doc")
    if "updated_by" in kdoc:
        op.drop_column("knowledge_doc", "updated_by")
    if "updated_at" in kdoc:
        op.drop_column("knowledge_doc", "updated_at")
    if "sort_order" in kdoc:
        op.drop_column("knowledge_doc", "sort_order")
    if "category" in kdoc:
        op.drop_column("knowledge_doc", "category")
