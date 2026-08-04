"""crm settings become a tenant's own: move the platform rows onto branch 1

The first client's CRM MCP endpoint — and the bearer token embedded in its URL — were
stored at the PLATFORM tier (branch_id NULL), which every branch inherits. Branch 1 is
the only branch that actually talks to that CRM (crm_read_enabled, crm_rescue_enabled and
crm_writeback_enabled are all on there and nowhere else), so the rows move onto branch 1
and branch 1 keeps resolving exactly the values it resolves today. Every other branch
stops inheriting a stranger's CRM link.

The code half of the change (settings.schema_crm.TENANT_ONLY_KEYS) makes the resolver
ignore platform-tier rows for these keys, so a row re-created at the platform tier later
cannot leak either.

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
_OWNER_BRANCH = 1


def upgrade() -> None:
    bind = op.get_bind()
    owner = bind.execute(
        sa.text("SELECT 1 FROM branch WHERE id = :b"), {"b": _OWNER_BRANCH}).first()
    if owner is None:
        return  # a fresh install has no branch 1 and no platform CRM rows to move
    keys = sa.bindparam("keys", expanding=True)
    # A branch-tier row already wins over the platform one, so where both exist the platform
    # row is redundant — dropping it first also keeps the UPDATE off the uq_setting_scope
    # unique index.
    bind.execute(
        sa.text(
            "DELETE FROM app_setting WHERE branch_id IS NULL AND key IN :keys"
            " AND EXISTS (SELECT 1 FROM app_setting b WHERE b.branch_id = :owner"
            "             AND b.channel_id IS NULL AND b.key = app_setting.key)"
        ).bindparams(keys),
        {"keys": list(_KEYS), "owner": _OWNER_BRANCH})
    bind.execute(
        sa.text("UPDATE app_setting SET branch_id = :owner"
                " WHERE branch_id IS NULL AND channel_id IS NULL AND key IN :keys"
                ).bindparams(keys),
        {"keys": list(_KEYS), "owner": _OWNER_BRANCH})


def downgrade() -> None:
    # Not reversible: putting a tenant's CRM credentials back on the platform tier is the
    # defect this migration exists to remove.
    pass
