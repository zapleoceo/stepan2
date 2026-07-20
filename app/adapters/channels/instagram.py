"""Instagram follow-up adapter — private API (instagrapi) to bypass the 24h window.

The adapter never touches instagrapi: it maps a thin IGTransport's raw dicts to the
domain types, so replacing instagrapi (or stubbing it in tests) means swapping the
transport, not this class."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from app.domain.clock import as_naive_utc
from app.domain.enums import ChannelKind, SessionStatus
from app.ports.channel import InboundComment, InboundMessage, SendResult

# IG errors that mean the message is already gone — unsend is idempotent, don't retry.
_GONE_MARKERS = ("not found", "does not exist", "media_unavailable", "no longer available",
                 "already", "unsend")


def _already_gone(exc: Exception) -> bool:
    return any(m in str(exc).lower() for m in _GONE_MARKERS)


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

    async def fetch_own_comments(self, since_epoch_us: int | None) -> list[dict[str, Any]]:
        ...

    async def send_comment_reply(self, comment_id: str, text: str) -> dict[str, Any]:
        ...

    async def delete_comment(self, comment_id: str) -> None:
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
        """Unsend one of our messages in IG. True on success OR when IG says the message is
        already gone (idempotent — the goal is 'not in IG', which is met either way, so a
        message the manager already deleted in the app doesn't retry forever). False only on
        a real, retryable failure (e.g. a 403 action throttle)."""
        try:
            await self._t.revoke_direct(external_thread_id, external_message_id)
        except Exception as exc:  # noqa: BLE001 — classify: gone = done, else keep + retry
            if _already_gone(exc):
                return True
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
            "username": raw.get("username"),
            "full_name": raw.get("full_name"),
            "avatar_url": raw.get("avatar_url"),
        }

    async def download_media(self, url: str) -> bytes:
        return await self._t.download_media(url)

    async def fetch_comments(self, *, since: datetime | None = None) -> list[InboundComment]:
        raw = await self._t.fetch_own_comments(
            int(since.timestamp() * 1_000_000) if since else None)
        return [self._to_comment(c) for c in raw]

    async def reply_to_comment(self, comment_external_id: str, text: str) -> SendResult:
        try:
            raw = await self._t.send_comment_reply(comment_external_id, text)
        except Exception as exc:  # transport failure → caller decides retry/skip
            return SendResult(ok=False, error=str(exc))
        return SendResult(ok=True, external_message_id=str(raw.get("pk", "")))

    async def hide_comment(self, comment_external_id: str) -> SendResult:
        """Delete a spam/abuse comment under our own post. Idempotent: if IG says it's
        already gone, that's success (the goal — 'not visible' — is met either way)."""
        try:
            await self._t.delete_comment(comment_external_id)
        except Exception as exc:  # noqa: BLE001 — gone = done, else keep flag + retry
            if _already_gone(exc):
                return SendResult(ok=True)
            return SendResult(ok=False, error=str(exc))
        return SendResult(ok=True)

    def _to_comment(self, c: dict[str, Any]) -> InboundComment:
        return InboundComment(
            external_id=str(c["comment_id"]),
            media_id=str(c["media_id"]),
            text=str(c.get("text", "")),
            occurred_at=_as_dt(c.get("timestamp")),
            author_pk=str(c["author_pk"]) if c.get("author_pk") else None,
            author_username=c.get("author_username") or None,
            media_caption=c.get("media_caption") or None,
            media_permalink=c.get("media_permalink") or None,
        )

    def _to_inbound(self, thread: dict[str, Any]) -> InboundMessage:
        return InboundMessage(
            external_thread_id=str(thread["thread_id"]),
            sender_id=str(thread["sender_id"]),
            lead_ig_user_id=thread.get("lead_ig_user_id") or None,
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
    """IG timestamps are epoch microseconds."""
    return as_naive_utc(value, epoch_unit="us")
