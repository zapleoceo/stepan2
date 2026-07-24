"""free-only cleanup — dead column, dead settings rows, phantom KB skeleton docs

Follows the free-only cutover (PR #14). Three removals, all confirmed dead:

- lead.guard_regen_count: zero writers, zero readers (the per-lead routing idea it was
  reserved for never landed; routing reads the dossier).
- app_setting rows for keys the parser no longer knows: reply_mode, critic_gate,
  nudge_classifier_shadow, reply_guard, tech_usecase_enabled, tech_search_enabled,
  meta_ads_token — inert since the cutover, removed so the table matches the schema.
- knowledge_doc rows from the retired 14-doc canonical skeleton that are still EMPTY
  (content = '') — phantom editors for text the prompt never loaded. Docs with real
  content are kept untouched.

Revision ID: fr33tail001
Revises: mgrname0001
Create Date: 2026-07-25 18:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "fr33tail001"
down_revision = "mgrname0001"
branch_labels = None
depends_on = None

_DEAD_SETTING_KEYS = (
    "reply_mode", "critic_gate", "nudge_classifier_shadow", "reply_guard",
    "tech_usecase_enabled", "tech_search_enabled", "meta_ads_token",
)
_PHANTOM_DOC_SLUGS = (
    "faq", "market_facts", "stories", "market_competitors", "playbook_discovery",
    "sales_mastery", "playbook_qualify", "playbook_price", "playbook_close",
    "playbook_ready", "playbook_meetings", "playbook_format", "playbook_social",
)


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("lead")}
    if "guard_regen_count" in cols:
        op.drop_column("lead", "guard_regen_count")
    bind.execute(
        sa.text("DELETE FROM app_setting WHERE key IN :keys").bindparams(
            sa.bindparam("keys", expanding=True)),
        {"keys": list(_DEAD_SETTING_KEYS)})
    bind.execute(
        sa.text("DELETE FROM knowledge_doc WHERE slug IN :slugs"
                " AND (content IS NULL OR content = '')").bindparams(
            sa.bindparam("slugs", expanding=True)),
        {"slugs": list(_PHANTOM_DOC_SLUGS)})


def downgrade() -> None:
    # The deleted rows are unrecoverable by design (they were empty/inert); only the
    # column comes back.
    op.add_column("lead", sa.Column(
        "guard_regen_count", sa.Integer(), nullable=False, server_default="0"))
