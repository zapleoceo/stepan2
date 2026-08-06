"""WhatsApp follow-up adapter — self-hosted Evolution API to bypass the 24h window.

The adapter speaks only to a thin WhatsAppTransport; the Evolution HTTP details live in
the transport, so a different WA provider is a new transport, not a new adapter."""
from __future__ import annotations

from typing import Any, Protocol

from app.domain.clock import as_naive_utc
from app.domain.enums import ChannelKind, SessionStatus
from app.ports.channel import READ_ONLY_ERROR, InboundMessage, SendResult


class WhatsAppTransport(Protocol):
    """Raw Evolution API calls, decoupled from its HTTP shape."""

    async def fetch_messages(self) -> list[dict[str, Any]]:
        ...

    async def send_message(self, remote_jid: str, text: str) -> dict[str, Any]:
        ...

    async def connection_state(self) -> str:
        ...

    async def pair(self) -> str:
        ...

    async def forget(self) -> None:
        ...


class WhatsAppAdapter:
    """Implements app.ports.channel.ChannelPort for WA follow-up via Evolution API.

    read_only is per-INSTANCE, not per-connector: the follow-up number sends, the managers'
    numbers are linked only so we can read where a lead goes after Stepan hands over the
    phone. Same adapter, opposite permission — so it is a constructor argument, and flipping
    a number to a full channel later is a checkbox, not a code change."""

    kind: ChannelKind = ChannelKind.WHATSAPP

    def __init__(
        self, transport: WhatsAppTransport, *, instance: str, read_only: bool = False
    ) -> None:
        self._t = transport
        self._instance = instance
        self.read_only = read_only

    async def fetch_inbound(self) -> list[InboundMessage]:
        messages = await self._t.fetch_messages()
        return [self._to_inbound(m) for m in messages]

    async def send_text(self, external_thread_id: str, text: str) -> SendResult:
        if self.read_only:
            return SendResult(ok=False, error=READ_ONLY_ERROR)
        try:
            raw = await self._t.send_message(external_thread_id, text)
        except Exception as exc:  # transport failure → caller decides retry/hand-off
            return SendResult(ok=False, error=str(exc))
        key = raw.get("key") or {}
        return SendResult(ok=True, external_message_id=str(key.get("id", "")))

    async def session_status(self) -> SessionStatus:
        return _map_state(await self._t.connection_state())

    def _to_inbound(self, msg: dict[str, Any]) -> InboundMessage:
        return InboundMessage(
            external_thread_id=str(msg["remote_jid"]),
            sender_id=str(msg.get("sender_id", msg["remote_jid"])),
            text=str(msg.get("text", "")),
            occurred_at=as_naive_utc(msg.get("message_timestamp")),
            product_hint=msg.get("ad_product"),
            # Both sides of the chat, with the side named by the transport where the raw
            # fromMe flag is. On a read-only number the "out" half IS the subject of study:
            # it is the manager talking, not us.
            direction=str(msg.get("direction", "in")),
            external_id=msg.get("external_id"),
            media_kind=msg.get("media_kind"),
        )


def _map_state(state: str) -> SessionStatus:
    """Evolution reports 'open' when paired; 'connecting' means the QR/session lapsed."""
    if state == "open":
        return SessionStatus.ACTIVE
    if state == "connecting":
        return SessionStatus.CHALLENGE
    return SessionStatus.EXPIRED
