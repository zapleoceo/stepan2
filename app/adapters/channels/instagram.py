"""Instagram follow-up adapter — private API (instagrapi) to bypass the 24h window.

The adapter never touches instagrapi: it maps a thin IGTransport's raw dicts to the
domain types, so replacing instagrapi (or stubbing it in tests) means swapping the
transport, not this class."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol

from app.domain.enums import ChannelKind, SessionStatus
from app.ports.channel import InboundMessage, SendResult


class IGTransport(Protocol):
    """Raw Instagram calls, decoupled from instagrapi specifics."""

    async def fetch_threads(self) -> list[dict[str, Any]]:
        ...

    async def send_direct(self, thread_id: str, text: str) -> dict[str, Any]:
        ...

    async def account_health(self) -> str:
        ...


class InstagramAdapter:
    """Implements app.ports.channel.ChannelPort for IG follow-up via a private transport."""

    kind: ChannelKind = ChannelKind.INSTAGRAM

    def __init__(self, transport: IGTransport, *, handle: str) -> None:
        self._t = transport
        self._handle = handle

    async def fetch_inbound(self) -> list[InboundMessage]:
        threads = await self._t.fetch_threads()
        return [self._to_inbound(t) for t in threads]

    async def send_text(self, external_thread_id: str, text: str) -> SendResult:
        try:
            raw = await self._t.send_direct(external_thread_id, text)
        except Exception as exc:  # transport failure → caller decides retry/hand-off
            return SendResult(ok=False, error=str(exc))
        return SendResult(ok=True, external_message_id=str(raw.get("item_id", "")))

    async def session_status(self) -> SessionStatus:
        return _map_health(await self._t.account_health())

    def _to_inbound(self, thread: dict[str, Any]) -> InboundMessage:
        return InboundMessage(
            external_thread_id=str(thread["thread_id"]),
            sender_id=str(thread["sender_id"]),
            text=str(thread.get("text", "")),
            occurred_at=_as_dt(thread.get("timestamp")),
            product_hint=thread.get("ad_product"),
            sender_username=thread.get("sender_username") or None,
            sender_name=thread.get("sender_name") or None,
            sender_avatar=thread.get("sender_avatar") or None,
            ad_id=thread.get("ad_id") or None,
            ad_media_id=thread.get("ad_media_id") or None,
            ad_preview_url=thread.get("ad_preview_url") or None,
            lead_source=thread.get("lead_source") or None,
            direction=thread.get("direction") or "in",
            external_id=thread.get("item_id") or None,
        )


def _map_health(health: str) -> SessionStatus:
    """instagrapi surfaces re-login needs as a 'challenge' string; map to CHALLENGE."""
    if health == "ok":
        return SessionStatus.ACTIVE
    if health == "challenge":
        return SessionStatus.CHALLENGE
    return SessionStatus.EXPIRED


def _as_dt(value: Any) -> datetime:
    """IG epoch microseconds or ISO → aware datetime; missing → epoch (never crash)."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1_000_000, tz=UTC)
    if isinstance(value, str) and value:
        return datetime.fromisoformat(value)
    return datetime.fromtimestamp(0, tz=UTC)
