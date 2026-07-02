"""Meta Conversions API — fire a server-side Lead event on real hand-off.

Feeds ad optimization: Meta learns which ads produce leads that actually reach a
manager. Config comes from branch settings (meta_pixel_id + meta_capi_token). Same
contract as the other transports: lazy httpx, log-and-False on failure, never raises.
"""
from __future__ import annotations

import hashlib
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

_GRAPH = "https://graph.facebook.com/v18.0"


def hash_phone(phone: str | None) -> str | None:
    """CAPI user_data.ph — sha256 of the digits-only international number."""
    if not phone:
        return None
    digits = "".join(ch for ch in phone if ch.isdigit())
    if len(digits) < 9:
        return None
    return hashlib.sha256(digits.encode()).hexdigest()


def build_event(
    *, event_name: str, event_id: str, phone: str | None, source_url: str | None = None,
) -> dict[str, Any]:
    """One CAPI event dict — split out pure so tests cover the exact payload shape."""
    user_data: dict[str, Any] = {}
    ph = hash_phone(phone)
    if ph:
        user_data["ph"] = [ph]
    event: dict[str, Any] = {
        "event_name": event_name,
        "event_time": int(time.time()),
        "event_id": event_id,  # dedup key — resend of the same handoff is idempotent
        "action_source": "chat",
        "user_data": user_data,
    }
    if source_url:
        event["event_source_url"] = source_url
    return event


class MetaCapi:
    """Send events to a branch's pixel; misconfiguration = quiet no-op (False)."""

    async def send_lead(
        self,
        pixel_id: str,
        token: str,
        *,
        event_id: str,
        phone: str | None = None,
        event_name: str = "Lead",
    ) -> bool:
        if not pixel_id or not token:
            return False
        payload = {"data": [build_event(event_name=event_name, event_id=event_id, phone=phone)]}
        return await self._post(pixel_id, token, payload)

    async def _post(self, pixel_id: str, token: str, payload: dict[str, Any]) -> bool:
        import httpx  # lazy: keep the module importable without the dep

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.post(
                    f"{_GRAPH}/{pixel_id}/events",
                    params={"access_token": token},
                    json=payload,
                )
            response.raise_for_status()
        except Exception as exc:  # noqa: BLE001 — ad-tracking must never break handoff
            logger.warning("meta capi send failed (pixel=%s): %s", pixel_id, exc)
            return False
        return True
