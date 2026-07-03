"""Meta Business adapter — official Graph API, the READ path for all messages.

Reading is canonical via Graph; sending here is only the in-window reply. The adapter
maps a thin GraphTransport's payloads, so Graph version bumps stay in the transport."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol

from app.domain.clock import naive_utc
from app.domain.enums import ChannelKind, SessionStatus
from app.ports.channel import InboundMessage, SendResult


class GraphTransport(Protocol):
    """Raw Graph API calls, decoupled from endpoint/version specifics."""

    async def fetch_conversations(self) -> list[dict[str, Any]]:
        ...

    async def send_message(self, recipient_id: str, text: str) -> dict[str, Any]:
        ...

    async def token_debug(self) -> dict[str, Any]:
        ...


class MetaBusinessAdapter:
    """Implements app.ports.channel.ChannelPort over the official Graph API (read path)."""

    kind: ChannelKind = ChannelKind.META_BUSINESS

    def __init__(self, transport: GraphTransport, *, account_id: str) -> None:
        self._t = transport
        self._account_id = account_id

    async def fetch_inbound(self) -> list[InboundMessage]:
        conversations = await self._t.fetch_conversations()
        return [self._to_inbound(c) for c in conversations]

    async def send_text(self, external_thread_id: str, text: str) -> SendResult:
        try:
            raw = await self._t.send_message(external_thread_id, text)
        except Exception as exc:  # transport failure → caller decides retry/hand-off
            return SendResult(ok=False, error=str(exc))
        if raw.get("error"):
            return SendResult(ok=False, error=str(raw["error"]))
        return SendResult(ok=True, external_message_id=str(raw.get("message_id", "")))

    async def session_status(self) -> SessionStatus:
        debug = await self._t.token_debug()
        return _map_token(debug)

    def _to_inbound(self, conv: dict[str, Any]) -> InboundMessage:
        return InboundMessage(
            external_thread_id=str(conv["thread_id"]),
            sender_id=str(conv["from_id"]),
            text=str(conv.get("message", "")),
            occurred_at=_as_dt(conv.get("created_time")),
            product_hint=conv.get("referral_product"),
        )


def _map_token(debug: dict[str, Any]) -> SessionStatus:
    """Graph token debug: invalid token → CHALLENGE (re-auth), expired window → EXPIRED."""
    if not debug.get("is_valid", False):
        return SessionStatus.CHALLENGE
    if debug.get("window_open", True):
        return SessionStatus.ACTIVE
    return SessionStatus.EXPIRED


def _as_dt(value: Any) -> datetime:
    """Graph epoch seconds or ISO-8601 → aware datetime; missing → epoch (never crash)."""
    if isinstance(value, datetime):
        return naive_utc(value)
    if isinstance(value, (int, float)):
        return naive_utc(datetime.fromtimestamp(value, tz=UTC))
    if isinstance(value, str) and value:
        return naive_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    return datetime.fromtimestamp(0, tz=UTC).replace(tzinfo=None)
