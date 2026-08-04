"""Settings keys that belong to ONE tenant and are never inherited from the platform tier.

The first client's CRM endpoint — and the bearer token embedded in its URL — lived as
app_setting rows with branch_id NULL, so every branch resolved them as its own: any tenant
that switched the CRM read-gate on would have started asking a stranger's CRM about its
leads, and any operator with branch access could read the token.

This is a data-scoping rule, so it sits next to the repository that enforces it rather than
in the module that renders the form fields — the repository must not have to import the UI
schema to know what it may not merge.
"""
from __future__ import annotations

TENANT_ONLY_KEYS: frozenset[str] = frozenset({
    "crm_enabled", "crm_webhook_url",
    "crm_read_enabled", "crm_state_url", "crm_read_secret",
    "crm_mcp_url", "crm_mcp_city_alias",
    "crm_rescue_enabled", "crm_writeback_enabled",
})
