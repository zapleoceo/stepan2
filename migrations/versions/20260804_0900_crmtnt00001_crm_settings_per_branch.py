"""crm settings become a tenant's own: move the platform rows onto the branch that uses them

The first client's CRM MCP endpoint — and the bearer token embedded in its URL — were
stored at the PLATFORM tier (branch_id NULL), which every branch inherits.

The owning branch is DERIVED, not assumed: it is the branch that has a CRM feature switched
on at its own tier (on production that is branch 1, and only branch 1). Hardcoding an id
would, on a restored snapshot or a staging copy where branch 1 is somebody else, hand that
stranger the bearer token — the exact failure this migration exists to remove. If no branch
has the CRM on, or more than one does, nothing moves: the platform rows stay put and the
code half below makes them inert anyway.

The code half (settings.tenant_keys.TENANT_ONLY_KEYS) makes the resolver ignore
platform-tier rows for these keys, so a row re-created at the platform tier later cannot
leak either.

Revision ID: crmtnt00001
Revises: dossbf00001
Create Date: 2026-08-04 09:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "crmtnt00001"
down_revision = "dossbf00001"
branch_labels = None
depends_on = None

_KEYS = (
    "crm_enabled", "crm_webhook_url",
    "crm_read_enabled", "crm_state_url", "crm_read_secret",
    "crm_mcp_url", "crm_mcp_city_alias",
    "crm_rescue_enabled", "crm_writeback_enabled",
)
# Switching any of these on is what makes a branch actually talk to the CRM, so a branch
# holding one of them is the branch whose credentials the platform rows are.
_OWNERSHIP_KEYS = ("crm_read_enabled", "crm_rescue_enabled", "crm_writeback_enabled")

# What the platform row carried before this migration. The alias has never had an editor, so
# the only way a row exists is a hand-written INSERT; if the owner ends up without one it
# inherits the schema default, which is now empty because a per-branch key cannot ship one
# tenant's city. Empty flows unguarded into crm_client_search, crm_lead_add_event and the read
# gate, and the resulting failure is swallowed — the CRM just stops answering, quietly.
_ALIAS_FALLBACK = "jakarta"


def _crm_owner(bind: sa.engine.Connection) -> int | None:
    rows = bind.execute(
        sa.text("SELECT DISTINCT branch_id FROM app_setting"
                " WHERE branch_id IS NOT NULL AND channel_id IS NULL"
                "   AND key IN :keys AND lower(value) = 'true'"
                ).bindparams(sa.bindparam("keys", expanding=True)),
        {"keys": list(_OWNERSHIP_KEYS)}).all()
    return rows[0][0] if len(rows) == 1 else None


def upgrade() -> None:
    bind = op.get_bind()
    owner = _crm_owner(bind)
    if owner is None:
        return  # nobody (fresh install) or several — guessing is what we are removing
    keys = sa.bindparam("keys", expanding=True)
    # A branch-tier row already wins over the platform one, so where both exist the platform
    # row is redundant — dropping it first also keeps the UPDATE off the uq_setting_scope
    # unique index. Same predicate as the UPDATE: whatever this does not re-parent, it must
    # not delete either, because there is no way to get it back.
    bind.execute(
        sa.text(
            "DELETE FROM app_setting"
            " WHERE branch_id IS NULL AND channel_id IS NULL AND key IN :keys"
            " AND EXISTS (SELECT 1 FROM app_setting b WHERE b.branch_id = :owner"
            "             AND b.channel_id IS NULL AND b.key = app_setting.key)"
        ).bindparams(keys),
        {"keys": list(_KEYS), "owner": owner})
    bind.execute(
        sa.text("UPDATE app_setting SET branch_id = :owner"
                " WHERE branch_id IS NULL AND channel_id IS NULL AND key IN :keys"
                ).bindparams(keys),
        {"keys": list(_KEYS), "owner": owner})
    bind.execute(
        sa.text("INSERT INTO app_setting (branch_id, channel_id, key, value)"
                " SELECT :owner, NULL, 'crm_mcp_city_alias', :fallback"
                " WHERE NOT EXISTS (SELECT 1 FROM app_setting"
                "                   WHERE branch_id = :owner AND channel_id IS NULL"
                "                     AND key = 'crm_mcp_city_alias')"),
        {"owner": owner, "fallback": _ALIAS_FALLBACK})


def downgrade() -> None:
    # Deliberately a no-op, and safe as one. Nothing records which branch rows this
    # migration created, so a reversal cannot tell them from the CRM rows the branch always
    # had (crm_read_enabled and friends) — re-parenting those onto the platform tier would
    # publish the branch's own credentials to every tenant. Leaving the data where it is
    # costs nothing on rollback: the pre-crmtnt00001 resolver reads the branch tier ahead of
    # the platform tier, so the owning branch resolves the same values either way, and the
    # other branches merely stop inheriting a CRM that was never theirs.
    pass
