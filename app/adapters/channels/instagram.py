"""Instagram follow-up adapter — private API (instagrapi) to bypass the 24h window.

The adapter never touches instagrapi: it maps a thin IGTransport's raw dicts to the
domain types, so replacing instagrapi (or stubbing it in tests) means swapping the
transport, not this class."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol

from app.domain.clock import naive_utc
from app.domain.enums import ChannelKind, SessionStatus
from app.ports.channel import InboundMessage, SendResult


class IGTransport(Protocol):
    """Raw Instagram calls, decoupled from instagrapi specifics."""

    async def fetch_threads(self) -> list[dict[str, Any]]:
        ...

    async def send_direct(self, thread_id: str, text: str) -> dict[str, Any]:
        ...

    async def revoke_direct(self, thread_id: str, item_id: str) -> None:
        ...

    async def mark_seen(self, thread_id: str) -> None:
        ...

    async def account_health(self) -> str:
        ...

    async def fetch_user_stats(self, ig_user_id: str) -> dict[str, Any]:
        ...

    async def download_media(self, url: str) -> bytes:
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

    async def revoke(self, external_thread_id: str, external_message_id: str) -> bool:
        """Unsend one of our messages in IG; False on transport failure (keep + retry)."""
        try:
            await self._t.revoke_direct(external_thread_id, external_message_id)
        except Exception:  # noqa: BLE001 — worker logs, keeps the flag, retries next tick
            return False
        return True

    async def mark_seen(self, external_thread_id: str) -> None:
        """Best-effort read receipt before replying; never blocks a send on failure."""
        try:
            await self._t.mark_seen(external_thread_id)
        except Exception as exc:  # noqa: BLE001
            import logging  # noqa: PLC0415

            logging.getLogger(__name__).debug("mark_seen failed: %s", exc)

    async def session_status(self) -> SessionStatus:
        return _map_health(await self._t.account_health())

    async def fetch_profile(self, ig_user_id: str) -> dict[str, Any] | None:
        """Follower/following counts for a lead; None on transport failure (logged)."""
        try:
            raw = await self._t.fetch_user_stats(ig_user_id)
        except Exception as exc:  # noqa: BLE001 — loop keeps going, lead untouched
            import logging  # noqa: PLC0415

            logging.getLogger(__name__).warning(
                "IG fetch_profile failed user=%s: %s", ig_user_id, exc)
            return None
        return {
            "follower_count": raw.get("follower_count"),
            "following_count": raw.get("following_count"),
        }

    async def download_media(self, url: str) -> bytes:
        return await self._t.download_media(url)

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
            link_url=thread.get("link_url") or None,
            preview_url=thread.get("preview_url") or None,
            media_url=thread.get("media_url") or None,
            media_kind=thread.get("media_kind") or None,
            lead_seen_at=_as_dt(thread["lead_seen_at"]) if thread.get("lead_seen_at")
            else None,
        )


def _map_health(health: str) -> SessionStatus:
    """instagrapi surfaces re-login needs as a 'challenge' string; map to CHALLENGE."""
    if health == "ok":
        return SessionStatus.ACTIVE
    if health == "challenge":
        return SessionStatus.CHALLENGE
    return SessionStatus.EXPIRED


def _as_dt(value: Any) -> datetime:
    """IG epoch microseconds or ISO → naive UTC datetime; missing → epoch (never crash)."""
    if isinstance(value, datetime):
        return naive_utc(value)
    if isinstance(value, (int, float)):
        return naive_utc(datetime.fromtimestamp(value / 1_000_000, tz=UTC))
    if isinstance(value, str) and value:
        return naive_utc(datetime.fromisoformat(value))
    return datetime.fromtimestamp(0, tz=UTC).replace(tzinfo=None)
