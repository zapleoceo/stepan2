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


class CrmReader:
    """Read the current state of a lead from the branch CRM (GET by phone).

    Same never-raises contract as the push side: a transport error / bad payload returns
    None, and the caller treats an unreachable CRM as 'no opinion' (allow the send) so a
    CRM outage never silences Stepan."""

    async def get_state(self, url: str, secret: str, phone: str) -> dict | None:
        import httpx  # lazy

        headers = {"Authorization": f"Bearer {secret}"} if secret else {}
        try:
            async with httpx.AsyncClient(timeout=self._timeout()) as client:
                response = await client.get(url, params={"phone": phone}, headers=headers)
            if response.status_code == 404:  # lead simply not in CRM
                return {"exists": False}
            response.raise_for_status()
            data = response.json()
            return data if isinstance(data, dict) else None
        except Exception as exc:  # noqa: BLE001 — treat as 'no opinion', retry next tick
            # Log neither the raw phone (PII) nor `exc` (its URL embeds the phone query param);
            # a masked tail + the exception type is enough to diagnose a flaky CRM endpoint.
            masked = "…" + phone[-4:] if phone and len(phone) >= 4 else "?"
            logger.warning("crm read failed (phone=%s): %s", masked, type(exc).__name__)
            return None

    @staticmethod
    def _timeout() -> float:
        from app.config import settings  # lazy to avoid import at module load
        return settings().crm_read_timeout_s
