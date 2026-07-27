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

from app.config import settings

logger = logging.getLogger(__name__)


def _graph_base() -> str:
    # Lazy (not module-level) — matches the other adapters and keeps this module importable
    # before config/env is initialized (e.g. test collection); also picks up a version change.
    return f"https://graph.facebook.com/{settings().meta_graph_version}"


def capi_token(cfg: Any) -> str:
    """The token to send events with — the System User one, falling back to the legacy field.

    `meta_capi_token` was superseded by `meta_system_user_token` (the settings schema marks it
    "legacy — use the System User token above" and hides it), but the send path kept reading
    the old field. On this branch it held `1q2w#E$R` — eight characters, a keyboard walk left
    behind when someone filled the form.

    Eight characters is truthy, so the guard passed and every hand-off posted to Meta and got
    back `401 Unauthorized`. All 76 of them, silently: the adapter logs a warning and returns
    False by design, so ad tracking can never break a hand-off, and Docker log rotation carried
    the warnings away. Meta received nothing, and the campaigns optimised on the only signal
    they had — a message being started, which is what we have far too many of.

    The System User token is valid, carries ads_management, and does not expire."""
    return (getattr(cfg, "meta_system_user_token", "") or "").strip() \
        or (getattr(cfg, "meta_capi_token", "") or "").strip()


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
        """One event. `event_id` is the dedup key, so a resend of the same fact is free.

        Three names are sent by this app, and the split matters because Meta needs roughly 50
        events a week per ad set to leave the learning phase:

          QualifiedLead  a lead wrote in their own words and the extractor got an intent out
                         of it — ~200/week, which is the only one of the three with enough
                         volume to actually optimise a campaign on
          Lead           handed to a manager — ~14/week, too sparse to optimise on alone
          Purchase       the CRM reports deal_won — units per week, useful for Lookalike
                         audiences and for a revenue report, not for bidding

        Until this month none of them arrived at all: the send used a legacy token field
        holding an eight-character placeholder and every call 401'd (see capi_token)."""
        if not pixel_id or not token:
            return False
        payload = {"data": [build_event(event_name=event_name, event_id=event_id, phone=phone)]}
        return await self._post(pixel_id, token, payload)

    async def _post(self, pixel_id: str, token: str, payload: dict[str, Any]) -> bool:
        import httpx  # lazy: keep the module importable without the dep

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.post(
                    f"{_graph_base()}/{pixel_id}/events",
                    headers={"Authorization": f"Bearer {token}"},  # NOT ?access_token= — a
                    # query-string token lands in the exception URL and then in the log below.
                    json=payload,
                )
            response.raise_for_status()
        except Exception as exc:  # noqa: BLE001 — ad-tracking must never break handoff
            logger.warning("meta capi send failed (pixel=%s): %s", pixel_id, exc)
            return False
        return True
