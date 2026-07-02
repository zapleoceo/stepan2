"""CRM webhook transport — POST one manager-alert payload to the branch's CRM URL.

Same graceful-degradation contract as TelegramNotifier: httpx is imported lazily,
transport failure logs a warning and returns False — the alert row stays unsynced and
the next sync tick retries it. Never raises."""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class CrmWebhook:
    """Push alerts to an owner-configured webhook (setting crm_webhook_url)."""

    async def post_alert(self, url: str, payload: dict[str, Any]) -> bool:
        import httpx  # lazy: keep the module importable without the dep

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.post(url, json=payload)
            response.raise_for_status()
        except Exception as exc:  # noqa: BLE001 — any transport error = retry later
            logger.warning("crm push failed (alert id=%s): %s", payload.get("id"), exc)
            return False
        return True
